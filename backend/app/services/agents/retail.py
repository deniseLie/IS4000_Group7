"""Personal Complex Banking Needs answers use real scoped reads and the shared model/answer validators."""
import json
from decimal import Decimal
from zoneinfo import ZoneInfo
from app.core import data_api
from app.core.config import settings
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.events import Source
from app.schemas.retail import RetailRecords
from app.services import kb,knowledge,llm_gateway,prompts
from app.services.agents import retail_checks,retail_records
from app.services.agents.answer import draft_schema,validate_draft

PROMPT='v5'
LINKED='\nLinked context (DemoBank rules, not the customer): the customer reported a suspected unauthorised card charge in the same message.'


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


async def fall_below_threshold(ctx: RequestContext) -> tuple[Decimal,str] | None:
    """The approved savings threshold: a knowledge fact in database mode, the same demo term in memory mode."""
    if settings.knowledge_store=='database':
        with data_api.connect(ctx) as db:fact=await kb.get_fact('savings.fall_below_threshold',ctx,db)
        return None if fact is None or isinstance(fact.value,str) else (Decimal(str(fact.value)),fact.unit)
    value,unit,_=knowledge.FACTS['savings.fall_below_threshold']
    return Decimal(str(value)),unit


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Retail task question must be text')
    records=await retail_records.load(ctx)
    evidence=[*public_evidence,*evidence_for(records)]
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    if retail_checks.relevant(records,question,today):
        computed=retail_checks.checks(records,question,today,await fall_below_threshold(ctx))
        if computed is not None:evidence.append(computed)
    pv=prompts.load('retail',PROMPT)
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    if task.entities.get('linked')=='card_fraud':system+=LINKED
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],draft_schema(evidence))
    return validate_draft(task,json.dumps(response.data,ensure_ascii=False),evidence),evidence,pv['id']
