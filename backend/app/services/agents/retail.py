"""Personal Everyday Banking answers use real scoped reads and the shared model/answer validators."""
import json
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.events import Source
from app.schemas.retail import RetailRecords
from app.services import llm_gateway,prompts
from app.services.agents import retail_records
from app.services.agents.answer import draft_schema,validate_draft

PROMPT='v3'


def evidence_for(records: RetailRecords) -> list[Evidence]:
    rows=[('profile','Authenticated synthetic profile',records.profile.model_dump_json()),
        ('accounts','Authenticated synthetic accounts',json.dumps([row.model_dump(mode='json') for row in records.accounts])),
        ('cards','Authenticated synthetic cards; currency unrecorded',json.dumps([row.model_dump(mode='json') for row in records.cards])),
        ('loans','Authenticated synthetic loans; currency unrecorded',json.dumps([row.model_dump(mode='json') for row in records.loans])),
        ('transactions','Authenticated synthetic recent transactions',json.dumps([row.model_dump(mode='json') for row in records.transactions]))]
    return [Evidence(id='retail-'+key,text='{"records":'+text+'}',publisher='DemoBank synthetic records',
        scope='Own customer records from separate audited reads; at most 200 per collection. Transactions cover the last 90 days through today in Singapore. No product catalogue.',
        source=Source(id='retail-'+key,title=title,url=None,version=records.captured_at.isoformat(),publisher='DemoBank synthetic records'))
        for key,title,text in rows]


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Retail task question must be text')
    records=await retail_records.load(ctx)
    evidence=[*public_evidence,*evidence_for(records)]
    pv=prompts.load('retail',PROMPT)
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],draft_schema(evidence))
    return validate_draft(task,json.dumps(response.data,ensure_ascii=False),evidence),evidence,pv['id']
