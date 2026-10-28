"""Generate and validate one agent's public answer; never substitute a stored article for AI."""
import json
from pydantic import JsonValue, ValidationError
from app.core.observability import log_event
from app.schemas.agents import AgentDraft, Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult, Citation, Task
from app.services import llm_gateway, prompts
from app.services.answers import ACTION_CLAIM, URL, check_answer
from app.services.privacy import redact
from app.services.agents.registry import AGENT_FOR, AgentName

WITHHELD = "Sorry, I couldn't confirm this part of my answer, so I won't guess."


def response_prompt_id(name: AgentName) -> str:
    """Identify every released prompt fragment used for the agent's answer."""
    return '/'.join((prompts.current(name)['id'],prompts.current('domain_common')['id'],prompts.load('answer','v3')['id']))


def draft_schema(evidence: list[Evidence]) -> dict[str, JsonValue]:
    """The provider receives only the strict schema supported by the existing gateway."""
    ids = [item.id for item in evidence]
    return {'title':'domain_answer','type':'object','additionalProperties':False,
        'properties':{'answer':{'type':'string'},'source_ids':{'type':'array','items':{'type':'string', **({'enum':ids} if ids else {})}},
            'answer_kind':{'type':'string','enum':['banking','conversation']},
            'status':{'type':'string','enum':['ok','needs_clarification','needs_human']},'clarification_question':{'type':'string','description':'A new clarifying question for the customer, or an empty string when status is not needs_clarification. Never copy the assigned issue here.'}},
        'required':['answer','source_ids','answer_kind','status','clarification_question']}


def validate_draft(task: Task, raw: str, evidence: list[Evidence]) -> AgentResult:
    """Use exactly this agent's evidence for figures and citations; invalid content is withheld."""
    name = AGENT_FOR[task.domain]
    try:
        draft = AgentDraft.model_validate_json(raw)
    except ValidationError as error:
        log_event('agent_validation',agent=name,reason='draft_contract',error_types=[item['type'] for item in error.errors()])
        return AgentResult(task_id=task.task_id, agent=name, status='error', text=WITHHELD)
    data = [item.model_dump(exclude={'source'}) for item in evidence]
    answer, ids, reason = check_answer(raw, data)
    if reason:
        log_event('agent_validation',agent=name,reason=reason)
        return AgentResult(task_id=task.task_id, agent=name, status='error', text=WITHHELD)
    return AgentResult(task_id=task.task_id, agent=name, status=draft.status, answer_kind=draft.answer_kind,
        text=answer, question=draft.clarification_question if draft.status=='needs_clarification' else None,
        citations=[Citation(source_id=source_id) for source_id in dict.fromkeys(ids)])


async def respond(task: Task, question: str, history: list[Message], evidence: list[Evidence], language: str) -> AgentResult:
    name = AGENT_FOR[task.domain]
    pv = prompts.current(name)
    shared = prompts.load('answer', 'v3')
    common = prompts.current('domain_common')
    system = shared['system'] + shared['format'] + shared['public_only'] + pv['system'] + common['system']
    system += '\nAnswer language: ' + language
    system += '\nAssigned issue (untrusted data): ' + json.dumps(question, ensure_ascii=False)
    system += '\nEvidence (untrusted data): ' + json.dumps([item.model_dump(exclude={'source'}) for item in evidence], ensure_ascii=False)
    response = await llm_gateway.call({**pv,'id':response_prompt_id(name)}, system,
        [(m.role,m.content) for m in history], draft_schema(evidence))
    raw = json.dumps(response.data, ensure_ascii=False)
    result = validate_draft(task, raw, evidence)
    reason = check_answer(raw, [item.model_dump(exclude={'source'}) for item in evidence])[2]
    safe_draft = not redact(response.data['answer'])[1] and not URL.search(response.data['answer']) and not ACTION_CLAIM.search(response.data['answer'])
    if reason == 'source_check' and safe_draft:
        # Match the existing answer pipeline: one real revision, with the same validators.
        # Secret, URL and action-claim failures are never sent back as revision drafts.
        repair = common['repair'] + redact(raw)[0]
        log_event('agent_revision',agent=name,reason=reason)
        revised = await llm_gateway.call({**pv,'id':response_prompt_id(name)}, system+repair,
            [(m.role,m.content) for m in history], draft_schema(evidence))
        return validate_draft(task, json.dumps(revised.data,ensure_ascii=False), evidence)
    return result
