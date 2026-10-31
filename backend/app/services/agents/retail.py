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
from app.services.agents import account_answers,card_answers,request_status_answers,retail_checks,retail_records,transaction_answers,client_guidance
from app.services.agents.answer import checked_model_answer

PROMPT='v10'
LINKED='\nLinked context (DemoBank rules, not the customer): the customer reported a suspected unauthorised card charge in the same message.'


def currency_note(rows: list) -> str:
    """Card and loan currencies are recorded from migration 0083; an older database says so instead of guessing."""
    return '' if all(getattr(row,'currency',None) for row in rows) else '; currency unrecorded for some rows'


def evidence_for(records: RetailRecords) -> list[Evidence]:
    rows=[('profile','Authenticated synthetic profile',records.profile.model_dump_json()),
        ('accounts','Authenticated synthetic accounts',json.dumps([row.model_dump(mode='json') for row in records.accounts])),
        ('cards','Authenticated synthetic cards'+currency_note(records.cards),json.dumps([row.model_dump(mode='json') for row in records.cards])),
        ('loans','Authenticated synthetic loans'+currency_note(records.loans),json.dumps([row.model_dump(mode='json') for row in records.loans])),
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
    # Read existing request status from its authoritative ticket service *before*
    # card-limit intake or LLM generation. Errors are handled by orchestrator.
    status=await request_status_answers.respond(task.task_id,question,ctx)
    if status is not None:
        result,record_evidence=status
        return result,[*public_evidence,*record_evidence],prompts.load('retail',PROMPT)['id']
    guide=client_guidance.respond(task.task_id,question,public_evidence,ctx.received_at)
    if guide is not None:
        return guide,public_evidence,prompts.load('retail',PROMPT)['id']
    records=await retail_records.load(ctx)
    evidence=[*public_evidence,*evidence_for(records)]
    # Card figures and "which card?" come straight from her records (no model call, never withheld).
    direct=account_answers.respond(task.task_id,question,records,public_evidence)
    if direct is None:direct=card_answers.respond(task.task_id,question,history,records)
    if direct is None:
        latest=history[-1].content if history and history[-1].role=='user' else question
        direct=transaction_answers.respond(task.task_id,question,latest,records)
    if direct is not None:return direct,evidence,prompts.load('retail',PROMPT)['id']
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    if retail_checks.relevant(records,question,today):
        computed=retail_checks.checks(records,question,today,await fall_below_threshold(ctx))
        if computed is not None:evidence.append(computed)
    pv=prompts.load('retail',PROMPT)
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    if task.entities.get('linked')=='card_fraud':system+=LINKED
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    result=await checked_model_answer(task,system,history,evidence,pv)
    return result,evidence,pv['id']
