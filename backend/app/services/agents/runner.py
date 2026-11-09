"""The v5 agent runner (build plan phase 3): one runner for every agent in registry.CHAT_SPECS.

A known intent runs its default reads (arguments from the task's entities, never from the model) and makes one
answer call. Only an `open` task first runs a bounded tool loop over the spec's loop_tools. Both paths end in the
same validated answer (answer.checked_model_answer). orchestrator.agent calls it for every planned task except an
urgent card task (orchestrator.urgent_output)."""
import asyncio
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any
import httpx
from postgrest.exceptions import APIError
from pydantic import JsonValue, TypeAdapter, ValidationError
from app.core.config import settings
from app.core.observability import log_event, span
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import OPEN_INTENT, QUESTION, AgentOutput, AgentResult, AgentSpec, Export, PlanTask, Read, RequestContext, Task
from app.schemas.events import Source
from app.schemas.knowledge import KnowledgeSection
from app.core import data_api
from app.services import audit, customer_data, kb, llm_gateway, prompts, tools
from app.services.authorization import PolicyDenied
from app.services.llm_gateway import LLMError
from app.services.agents import derived, registry
from app.services.agents.answer import WITHHELD, checked_model_answer

PROMPTED = frozenset({'guide', 'accounts'})  # agents with their own runner prompt (version in prompts.VERSIONS); compliance and relationship answer through their rules only (agents/rules.py)
DOMAIN = {'guide': 'smalltalk', 'accounts': 'retail', 'compliance': 'compliance', 'relationship': 'relationship'}  # v4 Task shim
RECORDS = 'UBS Connect records'
TITLES = {'my_accounts': 'Your accounts', 'my_cards': 'Your cards', 'my_loans': 'Your loans', 'my_portfolio': 'Your investments', 'my_transactions': 'Your transactions',
    'request_categories': 'Request types'}
# Rules answer before any model call (architecture §5.6.2); a rule returns None to let the model answer.
# services/agents/rules.py registers them: (spec, task, ctx, history, language, inputs). inputs are the upstream exports
# this agent imports (wave 2); a rule's own exports go out only with an `ok` answer (rules.out), as on the model path.
Rule = Callable[[AgentSpec, PlanTask, RequestContext, list[Message], str, dict[str, Export]], Awaitable[AgentOutput | None]]
RULES: dict[str, tuple[Rule, ...]] = {}
# Expected provider and data failures withhold the section (the v4 dispatch_task list); anything else fails the turn.
FAILURES = (LLMError, TimeoutError, ValidationError, APIError, httpx.TransportError, PermissionError,
    customer_data.CustomerDataIntegrityError, kb.KnowledgeResponseError)


class PlanRefused(ValueError):
    """The task cannot run on this agent: wrong agent, unknown intent, no loop tools, an import it does not take, or no prompt."""


def check_task(spec: AgentSpec, task: PlanTask, inputs: dict[str, Export]) -> None:
    if task.agent != spec.name:raise PlanRefused(f'task {task.id} is for {task.agent}, not {spec.name}')
    if spec.name not in PROMPTED and not RULES.get(spec.name):raise PlanRefused(f'{spec.name} has no released runner prompt')
    if task.intent == OPEN_INTENT:
        if not spec.loop_tools:raise PlanRefused(f'{spec.name} answers listed intents only')
    elif task.intent not in spec.intents:raise PlanRefused(f'{spec.name} has no intent {task.intent!r}')
    if extra := set(inputs) - spec.imports:raise PlanRefused(f'{spec.name} does not import {sorted(extra)}')


def read_args(read: Read, task: PlanTask) -> dict[str, Any]:
    """A default read's arguments: fixed values, then entities; a missing entity leaves the tool's default."""
    args: dict[str, Any] = dict(read.fixed)
    for arg, entity in read.entities.items():
        value = task.question if entity == QUESTION else task.entities.get(entity)
        if value is not None:args[arg] = value
    return args


# ---- Evidence ----

def note(id_: str, text: str) -> Evidence:
    """A plain statement about a read (unavailable, refused); it carries no records or figures."""
    return Evidence(id=id_, text=text, publisher='UBS Connect', scope='Status of a read in this answer; not a record.',
        source=Source(id=id_, title='Read status', url=None, version='n/a', publisher='UBS Connect'))


def unique(base: str, taken: set[str]) -> str:
    id_, n = base, 1
    while id_ in taken:n += 1;id_ = f'{base}-{n}'
    taken.add(id_)
    return id_


def readable(row: Any) -> Any:
    """A signed transaction amount as the customer reads it: the amount without its sign, and its direction.
    The validator then accepts "SGD 152.40" for a recorded -152.4 debit."""
    if isinstance(row, dict) and isinstance(row.get('amount'), (int, float)) and not isinstance(row['amount'], bool) and 'direction' not in row:
        return {**row, 'amount': abs(row['amount']), 'direction': 'debit' if row['amount'] < 0 else 'credit'}
    return row


def records_evidence(name: str, args: dict[str, Any], result: dict[str, Any], ctx: RequestContext, taken: set[str]) -> list[Evidence]:
    if name == 'knowledge_search':
        found = [section for section in TypeAdapter(list[KnowledgeSection]).validate_python(result.get('items', [])) if section.cite not in taken]
        taken.update(section.cite for section in found)
        return [Evidence(id=s.cite, text=s.body, publisher=s.publisher, scope=s.scope, source=s.source()) for s in found]
    id_ = unique(name, taken)
    publisher = result.get('source') or RECORDS
    body = {key: value for key, value in result.items() if key not in {'call_id', 'as_of', 'source', 'items', 'data'}}  # read metadata is not a record
    if args:body['read_with'] = args  # e.g. the day window, so "the last 90 days" is supported
    records = result.get('items', result.get('data', []))
    body['records'] = [readable(row) for row in records] if isinstance(records, list) else readable(records)
    scope = ('The signed-in customer\'s own records from one audited read (read_with gives its arguments); today in Singapore is '
        f'{tools.today(ctx).isoformat()}. An empty or short list does not prove that other records do not exist.')
    return [Evidence(id=id_, text=json.dumps(body, ensure_ascii=False, default=str), publisher=publisher, scope=scope,
        source=Source(id=id_, title=TITLES.get(name, 'UBS Connect records'), url=None, version=tools.today(ctx).isoformat(), publisher=publisher))]


async def read(name: str, args: dict[str, Any], ctx: RequestContext, taken: set[str]) -> list[Evidence]:
    """One tool call as evidence. A failed or refused read becomes a note, so the answer says what it could not check."""
    try:
        if (spec := registry.spec_for(ctx.agent)) is not None and name in spec.tools:args = tools.bounded(name, args, spec.tools[name], ctx)
        return records_evidence(name, args, await tools.call(name, args, ctx), ctx, taken)
    except tools.ToolLimitExceeded as error:
        log_event('agent_read', agent=ctx.agent, tool=name, outcome='limit')
        return [note(unique(f'{name}-limit', taken), f'This read was not run because it asked for more than is allowed ({error}).')]
    except PolicyDenied:raise  # a tool outside the spec is a runner or spec bug, never a note
    except Exception as error:
        log_event('agent_read', agent=ctx.agent, tool=name, outcome='error', error_type=type(error).__name__)
        return [note(unique(f'{name}-unavailable', taken), 'These records could not be read right now.')]


def input_evidence(inputs: dict[str, Export]) -> list[Evidence]:
    """Values another agent computed earlier in the turn (passed by orchestrator.gather in wave 2)."""
    return [Evidence(id=f'input-{name}', text=json.dumps({name: export.value, 'source_ids': export.source_ids}, ensure_ascii=False),
        publisher=RECORDS, scope='A value another UBS Connect agent computed from cited records in this turn.',
        source=Source(id=f'input-{name}', title='From another part of this answer', url=None, version='n/a', publisher=RECORDS)) for name, export in inputs.items()]


# ---- The open-task loop: a pure state machine, so tests can feed it sample step strings ----

@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class LoopState:
    tools: frozenset[str]
    limit: int  # tool calls for the whole task
    used: int = 0
    steps: int = 0  # step calls made; at most `limit`, so refused steps cannot loop forever
    done: bool = False
    seen: frozenset[str] = frozenset()  # calls already run, as canonical JSON
    refused: tuple[tuple[str, str], ...] = ()  # (tool, reason) for every refused call or malformed step


def advance(state: LoopState, step_json: str) -> tuple[LoopState, list[Call]]:
    """Apply one step from the model: the calls to run now, and the next state.
    Refused calls (not a loop tool, bad arguments, a repeat or over the limit) are recorded and never run."""
    steps = state.steps + 1
    try:
        step = json.loads(step_json)
        if not isinstance(step, dict) or step.get('action') not in {'call', 'answer'} or not isinstance(step.get('calls', []), list):raise ValueError
    except ValueError:
        return replace(state, steps=steps, done=True, refused=(*state.refused, ('', 'malformed_step'))), []
    if step['action'] == 'answer' or not step.get('calls'):return replace(state, steps=steps, done=True), []
    calls, refused, seen, used = [], list(state.refused), set(state.seen), state.used
    for item in step['calls']:
        tool = item.get('tool') if isinstance(item, dict) else None
        try:args = json.loads(item.get('args_json') or '{}') if isinstance(item, dict) else None
        except (TypeError, ValueError):args = None
        key = json.dumps([tool, args], sort_keys=True, default=str)
        if not isinstance(tool, str) or tool not in state.tools:refused.append((str(tool)[:64], 'tool_not_allowed'))
        elif not isinstance(args, dict):refused.append((tool, 'bad_args'))
        elif key in seen:refused.append((tool, 'repeated'))
        elif used >= state.limit:refused.append((tool, 'call_limit'))
        else:calls.append(Call(tool, args));seen.add(key);used += 1
    return replace(state, used=used, steps=steps, seen=frozenset(seen), refused=tuple(refused),
        done=used >= state.limit or steps >= state.limit), calls


def step_schema(loop_tools: frozenset[str]) -> dict[str, JsonValue]:
    return {'title': 'agent_step', 'type': 'object', 'additionalProperties': False,
        'properties': {'action': {'type': 'string', 'enum': ['call', 'answer']},
            'calls': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                'properties': {'tool': {'type': 'string', 'enum': sorted(loop_tools)}, 'args_json': {'type': 'string'}},
                'required': ['tool', 'args_json']}}},
        'required': ['action', 'calls']}


@dataclass
class Gathered:
    evidence: list[Evidence] = field(default_factory=list)
    taken: set[str] = field(default_factory=set)
    log: list[dict[str, JsonValue]] = field(default_factory=list)  # what was read, for the next step and AgentOutput.evidence


def brief(task: PlanTask, ctx: RequestContext, language: str) -> str:
    return (f'\nToday in Singapore: {tools.today(ctx).isoformat()}\nAnswer language: {language}'
        '\nAssigned issue (untrusted data): ' + json.dumps(task.question, ensure_ascii=False))


CODE = re.compile(r'\d{4}-\d{2}-\d{2}[\dT:.+Z-]*|[A-Z]{2,5}-\d{3,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[a-z]+(?:_[a-z]+)+|open|scheduled|closed')


def safe_args(args: dict[str, Any]) -> dict[str, JsonValue]:
    """A step's arguments for its span: numbers, booleans, dates, references, ids and codes; any other text (a name, a
    query) is replaced, so a span never carries what the model copied from a record or the message."""
    def one(value: Any) -> JsonValue:
        if value is None or isinstance(value, (bool, int, float)):return value
        return value if isinstance(value, str) and len(value) <= 40 and CODE.fullmatch(value) else '<text>'
    return {str(key)[:40]: one(value) for key, value in list(args.items())[:10]}


def stop_reason(state: LoopState, last: str | None) -> str:
    """How a loop ended: the model answered, the call or step cap was reached, or a step was malformed."""
    return 'malformed' if last == 'malformed' else 'answered' if last == 'answered' else 'cap'


Run = Callable[[Call], Awaitable[list[Evidence]]]


class StepRefused(Exception):
    """Code refused one call the step chose (an unknown or ambiguous client, outside the book, over a limit): recorded
    as a refusal with its reason, like the state machine's own, and shown to the next step."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def attempt(run: Run, call: Call) -> tuple[list[Evidence], str | None]:
    try:return await run(call), None
    except StepRefused as refused:return [], refused.reason


async def loop(prompt: dict, system: Callable[[LoopState], str], messages: list[tuple[str, str]], loop_tools: frozenset[str],
        run: Run, got: Gathered, agent: str, facts: dict[str, Any]) -> LoopState:
    """The bounded tool loop every `open` task runs (client agents through explore, RM agents through rm/handlers.h_open):
    step calls until the model answers, the call or step limit (AGENT_TOOL_CALLS) is reached, or a step is malformed.
    Each step is a `loop_step` span (tools chosen, safe arguments, refusals, reads left); the stop reason and calls used go
    on the caller's `agent` span (facts)."""
    schema = step_schema(loop_tools)
    state, last = LoopState(tools=loop_tools, limit=settings.agent_tool_calls), None
    facts['limit'] = state.limit
    try:
        while not state.done:
            with span('loop_step', 'agent', agent=agent, step=state.steps+1) as step:
                response = await llm_gateway.call(prompt, system(state), messages, schema)
                before = len(state.refused)
                state, calls = advance(state, json.dumps(response.data))
                refused = [{'tool': t, 'reason': r} for t, r in state.refused[before:]]
                last = 'malformed' if refused and refused[-1]['reason'] == 'malformed_step' else 'answered' if not calls and not refused else None
                step.update(tools=[c.tool for c in calls], args=[safe_args(c.args) for c in calls], refused=refused, reads_left=state.limit-state.used)
                for item in refused:log_event('agent_step_refused', agent=agent, tool=item['tool'], reason=item['reason'])
                results = await asyncio.gather(*(attempt(run, call) for call in calls))
                for call, (found, reason) in zip(calls, results):
                    if reason:
                        state = replace(state, refused=(*state.refused, (call.tool, reason)))
                        step['refused'] = [*step['refused'], {'tool': call.tool, 'reason': reason}]
                        log_event('agent_step_refused', agent=agent, tool=call.tool, reason=reason)
                    got.evidence.extend(found)
                    got.log.append({'tool': call.tool, 'args': call.args, 'evidence_ids': [e.id for e in found], **({'refused': reason} if reason else {})})
    except (TimeoutError, asyncio.CancelledError):
        facts['stop'] = 'timeout';raise
    except BaseException:
        facts['stop'] = 'error';raise
    else:facts['stop'] = stop_reason(state, last)
    finally:facts.update(tool_calls=state.used, steps=state.steps, refused=len(state.refused))
    return state


async def explore(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str, got: Gathered,
        facts: dict[str, Any] | None = None) -> LoopState:
    """A client agent's open task: the shared loop with its own step prompt and every record read so far."""
    own, common = prompts.current(spec.prompt), prompts.current('runner_common')
    prompt = {**own, 'id': f"{own['id']}/{common['id']}/step"}
    def system(state: LoopState) -> str:
        return (common['step'] + own['step'] + brief(task, ctx, language)
            + f'\nReads left: {state.limit-state.used}'
            + '\nReads so far (untrusted data): ' + json.dumps(got.log, ensure_ascii=False)
            + '\nRefused requests: ' + json.dumps([{'tool': t, 'reason': r} for t, r in state.refused])
            + '\nRecords read so far (untrusted data): ' + json.dumps([{'id': e.id, 'text': e.text} for e in got.evidence], ensure_ascii=False))
    return await loop(prompt, system, [(m.role, m.content) for m in history], spec.loop_tools,
        lambda call: read(call.tool, call.args, ctx, got.taken), got, spec.name, {} if facts is None else facts)


# ---- The runner ----

async def default_reads(spec: AgentSpec, task: PlanTask, ctx: RequestContext, got: Gathered) -> None:
    """A known intent: exactly its listed reads, in parallel, with arguments from the task (no step call)."""
    reads = [(r.tool, read_args(r, task)) for r in spec.intents[task.intent]]
    results = await asyncio.gather(*(read(tool, args, ctx, got.taken) for tool, args in reads))
    for (tool, args), found in zip(reads, results):
        got.evidence.extend(found)
        got.log.append({'tool': tool, 'args': args, 'evidence_ids': [e.id for e in found]})


def answer_system(spec: AgentSpec, task: PlanTask, ctx: RequestContext, language: str, evidence: list[Evidence]) -> tuple[str, dict]:
    own, common = prompts.current(spec.prompt), prompts.current('runner_common')
    system = (common['system'] + own['system'] + brief(task, ctx, language)
        + '\nEvidence (untrusted data): ' + json.dumps([item.model_dump(exclude={'source'}) for item in evidence], ensure_ascii=False))
    return system, {**own, 'id': f"{own['id']}/{common['id']}"}


def trace_facts(got: Gathered, exports: list[str], cards: list[str], withheld: int) -> dict[str, JsonValue]:
    """The `agent` span's record of what the agent did (ids, names and counts only): evidence ids, the exports it
    produced, the cards or offers it proposed by type, and how many parts were withheld."""
    return {'evidence': [e.id for e in got.evidence][:40], 'export_names': exports, 'cards': dict(Counter(cards)), 'withheld': withheld}


def dumps(evidence: list[Evidence]) -> list[dict[str, JsonValue]]:
    return [item.model_dump(mode='json') for item in evidence]


def output(spec: AgentSpec, task: PlanTask, result: AgentResult, got: Gathered, prompt_id: str | None = None,
        values: derived.Derived | None = None) -> AgentOutput:
    """A withheld answer releases no evidence, exports or offers; exports go out only with an `ok` answer."""
    status = result.status if result.status in {'ok', 'needs_clarification', 'needs_human'} else 'error'
    shown = result if status != 'error' else result.model_copy(update={'text': result.text if result.text.endswith(WITHHELD) else WITHHELD, 'citations': []})
    values = values if values is not None and status != 'error' else derived.Derived()
    return AgentOutput(task_id=task.id, agent=spec.name, status=status, result=shown, evidence=dumps(got.evidence) if status != 'error' else [],
        reads=got.log, origin='validation' if status == 'error' else 'ai', prompt_id=prompt_id,
        exports=values.exports if status == 'ok' else {}, proposals=values.proposals)


def clarify(spec: AgentSpec, task: PlanTask, question: str, got: Gathered) -> AgentOutput:
    """A value code could not read (the trip dates, which card): a fixed question, no model call."""
    return AgentOutput(task_id=task.id, agent=spec.name, status='needs_clarification', origin='safety', reads=got.log,
        result=AgentResult(task_id=task.id, agent=spec.name, status='needs_clarification', answer_kind='conversation', text=question, question=question))


def reader(ctx: RequestContext, got: Gathered) -> derived.Reader:
    async def one(name: str, args: dict[str, Any]) -> list[Evidence]:
        found = await read(name, args, ctx, got.taken)
        got.log.append({'tool': name, 'args': args, 'evidence_ids': [e.id for e in found]})
        return found
    return one


def withheld(spec: AgentSpec, task: PlanTask, error: BaseException, got: Gathered, prompt_id: str | None) -> AgentOutput:
    fields = {'task_id': task.id, 'agent': spec.name, 'error_type': type(error).__name__,
        'db_code': data_api.database_code(error) if isinstance(error, APIError) else None}
    log_event('agent_failed', **fields)
    audit.add('agent', 'agent.failed', 'failed', details=fields)
    return output(spec, task, AgentResult(task_id=task.id, agent=spec.name, status='error', text=WITHHELD), got, prompt_id)


async def run_agent(spec: AgentSpec, task: PlanTask, inputs: dict[str, Export], ctx: RequestContext,
    history: list[Message] | None = None, language: str = 'English') -> AgentOutput:
    """Run one planned task on one agent, within AGENT_TIMEOUT_S. Raises PlanRefused before any read or model call."""
    check_task(spec, task, inputs)
    ctx = ctx.model_copy(update={'agent': spec.name})
    history = history or [Message(role='user', content=task.question)]
    got = Gathered()
    with span('agent', 'agent', agent=spec.name, intent=task.intent) as a:
        try:
            async with asyncio.timeout(settings.agent_timeout_s):
                for rule in RULES.get(spec.name, ()):
                    if (ruled := await rule(spec, task, ctx, history, language, inputs)) is not None:
                        a['path'], a['status'] = 'rule', ruled.status
                        a['cards'] = dict(Counter(p.type for p in ruled.proposals))
                        a['export_names'] = sorted(ruled.exports)
                        return ruled
                if spec.name not in PROMPTED:raise PlanRefused(f'{spec.name} has no runner prompt for {task.intent!r}')
                got.evidence.extend(input_evidence(inputs));got.taken.update(e.id for e in got.evidence)
                if task.intent == OPEN_INTENT:
                    a['path'] = 'loop'
                    await explore(spec, task, ctx, history, language, got, a)  # stop, tool_calls, limit, steps, refused
                else:
                    a['path'] = 'reads'
                    await default_reads(spec, task, ctx, got)
                values = await derived.compute(spec, task, ctx, got.evidence, reader(ctx, got))
                system, prompt = answer_system(spec, task, ctx, language, got.evidence)
                shim = Task(task_id=task.id, domain=DOMAIN[spec.name], intent=task.intent)
                result = await checked_model_answer(shim, system, history, got.evidence, prompt, agent=spec.name)
        except derived.Clarify as ask:
            a['status'] = 'needs_clarification'
            return clarify(spec, task, ask.question, got)
        except FAILURES as error:
            a['outcome'] = 'timeout' if isinstance(error, TimeoutError) else 'withheld'
            out = withheld(spec, task, error, got, prompt_id(spec))
            a['status'] = out.status
            return out
        a['status'] = result.status
        a['evidence_count'] = len(got.evidence)
        a['exports'] = len(values.exports) if result.status == 'ok' else 0
        a.update(trace_facts(got, sorted(values.exports) if result.status == 'ok' else [], [p.type for p in values.proposals], int(result.status == 'error')))
    return output(spec, task, result, got, prompt['id'], values)


def prompt_id(spec: AgentSpec) -> str | None:
    if spec.name not in PROMPTED:return None
    return f"{prompts.current(spec.prompt)['id']}/{prompts.current('runner_common')['id']}"
