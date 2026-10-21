"""Real-model fraud guidance with bounded proposals; no model response executes an action."""
import json
from pydantic import JsonValue
from app.schemas.agents import AgentDraft,Evidence
from app.schemas.chat import Message
from app.schemas.contracts import RequestContext,Task
from app.schemas.events import Source
from app.schemas.fraud import FraudDecision,FraudDraft,FraudProposal,SecurityRecords
from app.services import llm_gateway,prompts
from app.services.agents import fraud_records
from app.services.agents.answer import draft_schema,validate_draft


def evidence_for(records: SecurityRecords) -> Evidence:
    return Evidence(id='customer-records',text=records.model_dump_json(),publisher='DemoBank synthetic records',
        scope='Authenticated customer records; captured for this request only.',source=Source(id='customer-records',
        title='Authenticated synthetic card and transaction records',url=None,version=records.captured_at.isoformat(),
        publisher='DemoBank synthetic records'))


def schema_for(evidence: list[Evidence]) -> dict[str,JsonValue]:
    schema=draft_schema(evidence)
    properties=schema['properties']
    required=schema['required']
    if not isinstance(properties,dict) or not isinstance(required,list):raise ValueError('Domain schema requires properties and required fields')
    return {**schema,'properties':{**properties,'proposals':{'type':'array','items':{'type':'object',
        'additionalProperties':False,'properties':{'card_id':{'type':'string'},'transaction_id':{'type':'string'}},
        'required':['card_id','transaction_id']}}},'required':[*required,'proposals']}


def decision_for(task: Task,raw: str,evidence: list[Evidence],pairs: list[FraudProposal],prompt_id: str) -> FraudDecision:
    draft=FraudDraft.model_validate_json(raw)
    base=AgentDraft.model_validate(draft.model_dump(exclude={'proposals'}))
    result=validate_draft(task,base.model_dump_json(),evidence)
    proposal=draft.proposals[0] if draft.proposals else None
    if proposal is not None and proposal not in pairs:raise llm_gateway.LLMBadOutput('Fraud proposal is outside the eligible customer card/transaction pairs')
    return FraudDecision(result=result,proposal=proposal if result.status!='error' else None,prompt_id=prompt_id)


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[FraudDecision,list[Evidence]]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Fraud task question must be text')
    records=await fraud_records.load(ctx)
    evidence=[*public_evidence,evidence_for(records)]
    pairs=fraud_records.eligible_proposals(question,records)
    pv=prompts.load('fraud','v3')
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    system+='\nEligible pairs: '+json.dumps([item.model_dump() for item in pairs])
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],schema_for(evidence))
    return decision_for(task,json.dumps(response.data,ensure_ascii=False),evidence,pairs,pv['id']),evidence
