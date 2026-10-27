"""Real-model fraud guidance with bounded proposals; no model response executes an action."""
import json
from pydantic import JsonValue,ValidationError
from app.core.observability import log_event
from app.schemas.agents import AgentDraft,Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.events import Source
from app.schemas.fraud import FraudDecision,FraudDraft,FraudProposal,SecurityRecords
from app.services import audit,llm_gateway,prompts
from app.services.agents import fraud_records
from app.services.agents.answer import WITHHELD,draft_schema,validate_draft
from app.services.guardrail import FRAUD

PROMPT='v4'
# Fixed safety line kept first when a flagged fraud answer is withheld; it never replaces AI text.
URGENT='If you do not recognise a charge, contact your bank now through the official fraud line on the back of your card or its official app. Never share your PIN, password, one-time code or full card number.'


def evidence_for(records: SecurityRecords) -> Evidence:
    return Evidence(id='customer-records',text=records.model_dump_json(),publisher='DemoBank synthetic records',
        scope='Authenticated customer records; captured for this request only.',source=Source(id='customer-records',
        title='Authenticated synthetic card and transaction records',url=None,version=records.captured_at.isoformat(),
        publisher='DemoBank synthetic records'))


def pattern_evidence(records: SecurityRecords,txn_id: str) -> Evidence | None:
    """Rule-related small charges for the reported one; prose first so the trailing JSON stays readable to unit checks."""
    related=fraud_records.related_charges(records,txn_id)
    if not related:return None
    reported=next(item for item in records.transactions if item.txn_id==txn_id)
    fields={'date','description','amount','currency','card_last4'}
    rows=[*({'role':'earlier small charge',**item.model_dump(mode='json',include=fields)} for item in related),
        {'role':'reported charge',**reported.model_dump(mode='json',include=fields)}]
    text=(f'DemoBank rule check, not a fraud decision: {len(related)} smaller charges from the same merchant on the same card '
        f'in the {fraud_records.PATTERN_DAYS} days before the reported charge. Small charges like these before a larger one can match '
        'a card-testing pattern. '+json.dumps({'records':rows}))
    return Evidence(id='fraud-pattern',text=text,publisher='DemoBank rule check',
        scope='Deterministic rule over authenticated customer records; a pattern flag, not a fraud decision.',source=Source(id='fraud-pattern',
        title='Rule check: smaller charges from the same merchant',url=None,version=records.captured_at.isoformat(),publisher='DemoBank rule check'))


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
    pairs=fraud_records.eligible_proposals(question,records)
    latest=history[-1].content if history and history[-1].role=='user' else ''
    if len(pairs)>1 and len(narrowed:=fraud_records.eligible_proposals(latest,records))==1:pairs=narrowed
    flagged=bool(FRAUD.search(question) or FRAUD.search(latest))
    pattern=pattern_evidence(records,pairs[0].transaction_id) if len(pairs)==1 else None
    if pattern is not None:evidence.append(pattern)
    pv=prompts.load('fraud',PROMPT)
    system=pv['system']+'\nAnswer language: '+language
    if flagged and len(pairs)==1:system+='\nRecords-derived offers for review: temporary card block and dispute draft'
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    system+='\nEligible pairs: '+json.dumps([item.model_dump() for item in pairs])
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],schema_for(evidence))
    decision=decision_for(task,json.dumps(response.data,ensure_ascii=False),evidence,pairs,pv['id'],flagged)
    # The dispute draft, like the guaranteed block offer, comes from records for a flagged report, never from model text.
    if flagged and decision.proposal is not None:
        decision=decision.model_copy(update={'dispute':fraud_records.dispute_for(records,decision.proposal)})
    return decision,evidence
