"""Real-model fraud guidance with bounded proposals; no model response executes an action."""
import json
from pydantic import JsonValue,ValidationError
from app.core.observability import log_event
from app.schemas.agents import AgentDraft,Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.events import Source
from app.schemas.fraud import FraudDecision,FraudDraft,FraudProposal,SecurityRecords,SecurityTransaction
from app.services import audit,llm_gateway,prompts
from app.services.agents import fraud_records
from app.services.agents.answer import WITHHELD,draft_schema,validate_draft
from app.services.guardrail import FRAUD

PROMPT='v6'
# Fixed safety line kept first when a flagged fraud answer is withheld; it never replaces AI text.
URGENT='If you do not recognise a charge, contact your bank now through the official fraud line on the back of your card or its official app. Never share your PIN, password, one-time code or full card number.'


def evidence_for(records: SecurityRecords) -> Evidence:
    return Evidence(id='customer-records',text=records.model_dump_json(),publisher='DemoBank synthetic records',
        scope='Authenticated customer records; captured for this request only.',source=Source(id='customer-records',
        title='Authenticated synthetic card and transaction records',url=None,version=records.captured_at.isoformat(),
        publisher='DemoBank synthetic records'))


COUNTRIES={'SG':'Singapore','US':'the United States'}


def when(item: SecurityTransaction) -> str:
    """Both clock styles, so an answer saying 9:42 pm or 21:42 is supported by the rule evidence."""
    local=item.txn_at.astimezone(fraud_records.SGT)
    return f'{local:%Y-%m-%d} {local:%H:%M} SGT ({local.hour%12 or 12}:{local:%M} {"am" if local.hour<12 else "pm"})'


def travel_evidence(records: SecurityRecords,txn_id: str) -> Evidence | None:
    """The travel rule's flags for the reported charge; prose first so the trailing JSON stays readable to unit checks."""
    flags=fraud_records.flagged_with(records,txn_id)
    if not flags:return None
    anchor=flags[0].anchor
    def place(item: SecurityTransaction) -> str:return f'{item.merchant_country} ({COUNTRIES.get(item.merchant_country,item.merchant_country)})'
    # Charged amounts are listed unsigned as well, so an answer that echoes "a $2,400 charge" is supported without a sign flip.
    def charge(item: SecurityTransaction) -> str:
        return f'{when(item)} {item.description} in {place(item)}: {item.currency} {abs(item.amount):.2f} charged (recorded {item.currency} {item.amount:.2f})'
    def gap(flag: fraud_records.TravelFlag) -> str:
        hours,minutes=divmod(int(flag.gap.total_seconds())//60,60)
        return f'{hours} hours {minutes} minutes after the {anchor.merchant_country} charge (about {hours} hours)'
    other=[flag for flag in flags if flag.charge.txn_id!=txn_id]
    fields={'description','amount','currency','card_last4','merchant_country'}
    rows=[{'role':role,'time':when(item),**item.model_dump(mode='json',include=fields)}
        for role,item in [('earlier charge in another country',anchor),*(('flagged charge',flag.charge) for flag in flags)]]
    text=(f'DemoBank rule check, not a fraud decision: the card ending {anchor.card_last4} was used in {place(anchor)} and then in another '
        f'country less than {fraud_records.TRAVEL_HOURS} hours later, too soon to travel between them, so the charges in both places '
        'cannot all have been made in person by one cardholder. The rule cannot tell which place the cardholder was in. '
        f'Earlier charge: {charge(anchor)}. Flagged: '+'; '.join(f'{charge(flag.charge)}, {gap(flag)}' for flag in flags)+'. '
        +('Flagged with the reported charge as well: '+', '.join(flag.charge.description for flag in other)+'. ' if other else '')
        +json.dumps({'records':rows}))
    return Evidence(id='travel-check',text=text,publisher='DemoBank rule check',
        scope='Deterministic rule over authenticated customer records; an impossible-travel flag, not a fraud decision.',source=Source(id='travel-check',
        title='Rule check: card used in two countries too close in time',url=None,version=records.captured_at.isoformat(),publisher='DemoBank rule check'))


def schema_for(evidence: list[Evidence]) -> dict[str,JsonValue]:
    schema=draft_schema(evidence)
    properties=schema['properties']
    required=schema['required']
    if not isinstance(properties,dict) or not isinstance(required,list):raise ValueError('Domain schema requires properties and required fields')
    return {**schema,'properties':{**properties,'proposals':{'type':'array','items':{'type':'object',
        'additionalProperties':False,'properties':{'card_id':{'type':'string'},'transaction_id':{'type':'string'}},
        'required':['card_id','transaction_id']}}},'required':[*required,'proposals']}


def decision_for(task: Task,raw: str,evidence: list[Evidence],pairs: list[FraudProposal],prompt_id: str,flagged: bool=False) -> FraudDecision:
    """A rule-flagged report with exactly one eligible pair is always offered; offers execute nothing until confirmed.

    The offer comes from records, so a malformed or out-of-scope model draft withholds the text but keeps the offer.
    """
    try:
        draft=FraudDraft.model_validate_json(raw)
        base=AgentDraft.model_validate(draft.model_dump(exclude={'proposals'}))
        result=validate_draft(task,base.model_dump_json(),evidence)
        proposal=draft.proposals[0] if draft.proposals else None
        if proposal is not None and proposal not in pairs:raise llm_gateway.LLMBadOutput('Fraud proposal is outside the eligible customer card/transaction pairs')
    except (ValidationError,llm_gateway.LLMBadOutput) as error:
        if not (flagged and len(pairs)==1):raise
        fields={'task_id':task.task_id,'agent':'fraud','error_type':type(error).__name__,'db_code':None}
        log_event('agent_failed',**fields)
        audit.add('agent','agent.failed','failed',details=fields)
        return FraudDecision(result=AgentResult(task_id=task.task_id,agent='fraud',status='error',text=URGENT+' '+WITHHELD),
            proposal=pairs[0],prompt_id=prompt_id)
    if result.status=='error':proposal=None
    if flagged and proposal is None and len(pairs)==1:proposal=pairs[0]
    if flagged and result.status=='error':result=result.model_copy(update={'text':URGENT+' '+result.text})
    return FraudDecision(result=result,proposal=proposal,prompt_id=prompt_id)


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[FraudDecision,list[Evidence]]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Fraud task question must be text')
    records=await fraud_records.load(ctx)
    evidence=[*public_evidence,evidence_for(records)]
    latest=history[-1].content if history and history[-1].role=='user' else ''
    pairs=fraud_records.scoped_proposals(question,latest,records)
    travel=travel_evidence(records,pairs[0].transaction_id) if len(pairs)==1 else None
    if travel is not None:evidence.append(travel)
    # The travel rule over records flags a reported charge as firmly as the customer's own unrecognised-charge wording.
    flagged=bool(FRAUD.search(question) or FRAUD.search(latest) or travel is not None)
    pv=prompts.load('fraud',PROMPT)
    system=pv['system']+'\nAnswer language: '+language
    if flagged and len(pairs)==1:system+='\nThe case goes to the fraud team as urgent (the customer cannot block the card here)'
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    system+='\nEligible pairs: '+json.dumps([item.model_dump() for item in pairs])
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],schema_for(evidence))
    decision=decision_for(task,json.dumps(response.data,ensure_ascii=False),evidence,pairs,pv['id'],flagged)
    # The flagged charges for the fraud team's summary come from records for a flagged report, never from model text.
    if flagged and decision.proposal is not None:
        decision=decision.model_copy(update={'dispute':fraud_records.dispute_for(records,decision.proposal)})
    return decision,evidence
