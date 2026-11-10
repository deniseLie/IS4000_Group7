"""Minutes Writer model steps: every cue is processed; a later step consolidates supported claims without rewriting evidence.

The complete draft retains at most ten distinct decisions and ten actions, matching the existing approved RM writes.
Exceeding a bound fails explicitly. No action list, transcript or supporting quote is silently truncated.
"""
import asyncio,json,re
from datetime import date
from typing import Any,NamedTuple
from uuid import UUID,uuid5
from app.core.config import settings
from app.core.observability import log_event,span
from app.schemas.meeting_chunks import MinutesSelection
from app.schemas.meeting_lifecycle import Action,Cue,EvidenceClaim,GenerationContext,MinutesContent,MinutesProposal,ProposedAction
from app.services.agents.registry import MINUTES_OVERVIEW, MINUTES_WRITER
from app.services.llm_gateway import LLMResult,LLMTimeout,call
from app.services.prompts import load

PROMPT=MINUTES_WRITER.version

class Piece(NamedTuple):
    content: MinutesContent
    input_tokens: int|None
    output_tokens: int|None
    charged_tokens: int


def chunks(cues: tuple[Cue,...]) -> tuple[tuple[Cue,...],...]:
    result: list[tuple[Cue,...]]=[];current: tuple[Cue,...]=()
    for cue in cues:
        proposed=(*current,cue)
        size=len(json.dumps([item.model_dump(mode='json') for item in proposed],ensure_ascii=False).encode())
        if len(proposed)>80 or size>12000:
            if not current:raise ValueError('A speech cue exceeds the bounded model chunk; split it in a reviewed export')
            result.append(current);current=(cue,)
        else:current=proposed
    if current:result.append(current)
    if not result or len(result)>32:raise ValueError('Meeting evidence requires one to thirty-two bounded model chunks')
    return tuple(result)


def compose_recap(proposal: MinutesProposal) -> str:
    labels={'rm':'Your RM','client':'You','unassigned':'Owner to confirm'}
    parts=[' '.join(item.text for item in proposal.summary)]
    parts.extend('Agreed: '+item.text for item in proposal.decisions)
    parts.extend(labels[action.owner]+': '+action.text for action in proposal.actions if action.commitment=='agreed')
    return '\n'.join(parts)


def bind_action_ids(proposal: MinutesProposal,source_id: UUID) -> MinutesContent:
    actions=tuple(Action.model_validate({**action.model_dump(),'action_id':uuid5(source_id,json.dumps(action.model_dump(mode='json'),sort_keys=True))}) for action in proposal.actions)
    return MinutesContent(title=proposal.title,summary=proposal.summary,decisions=proposal.decisions,actions=actions,client_recap=compose_recap(proposal))


def estimated_charge(system: str,payload: str,schema: dict[str,object]) -> int:
    """A conservative charge for unknown usage; known provider usage remains separately recorded."""
    return len(system.encode())+len(payload.encode())+len(json.dumps(schema).encode())+settings.max_tokens


def model_schema(node: Any) -> Any:
    """The schema the model is sent: the same, without `pattern` keywords. On the API transport (OpenRouter) the providers
    reject MinutesProposal's ten `\\S` patterns as "Schema is too complex", and only after 2-3 minutes, so the gateway
    timeout reported it as a timeout (8 Nov 2026). Every reply is still validated against the full Pydantic model,
    which keeps the non-blank rule; a blank field fails as generated_draft_invalid."""
    if isinstance(node,dict):return {k:model_schema(v) for k,v in node.items() if not (k=='pattern' and isinstance(v,str))}
    if isinstance(node,list):return [model_schema(v) for v in node]
    return node


async def model_step(prompt: dict[str,str],system: str,payload: str,schema: dict[str,object]) -> LLMResult:
    try:result=await asyncio.wait_for(call(prompt,system,[('user',payload)],model_schema(schema),model=settings.meeting_model or None),timeout=240)
    except TimeoutError as error:raise LLMTimeout('Meeting model step exceeded 240 seconds; persisted completed chunks can be resumed') from error
    log_event('meeting_model_completed',prompt_version=prompt['id'],input_tokens=result.input_tokens,output_tokens=result.output_tokens,model=result.model,latency_ms=result.latency_ms)
    return result


HEDGES=re.compile(r'\b(?:if|unless|might|maybe|possibly)\b',re.I)
MONTHS=('january','february','march','april','may','june','july','august','september','october','november','december')
DATE_EVIDENCE=re.compile(r"\b(?:(\d{4}-\d{2}-\d{2})|(\d{1,2}) ("+'|'.join(MONTHS)+r") (\d{4}))\b",re.I)
DEADLINE_PREFIX=re.compile(r"\b(?:by|on|due(?: on)?|no later than)(?:\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday),?)?\s+$",re.I)


def quoted_dates(text: str,*,deadline: bool=False) -> dict[date,str]:
    """Only calendar dates explicitly present in the exact evidence, with no relative-date inference."""
    dates={}
    for match in DATE_EVIDENCE.finditer(text):
        if deadline and not DEADLINE_PREFIX.search(text[:match.start()]):continue
        iso,day,month,year=match.groups()
        try:due=date.fromisoformat(iso) if iso else date(int(year),MONTHS.index(month.lower())+1,int(day))
        except ValueError:continue
        dates[due]=match.group(0)
    return dates


def repair_actions(proposal: MinutesProposal,cues: tuple[Cue,...]) -> MinutesProposal:
    """Bring each proposed action within the SQL evidence rules (0121) instead of failing the whole draft.
    Recover an omitted deadline only from one explicit calendar date in the exact action quote. An unsupported date is dropped; an owner who
    did not say the quoted line becomes unassigned; hedged or unquoted conditions become tentative."""
    from pydantic import TypeAdapter
    adapter=TypeAdapter(ProposedAction);by_id={cue.cue_id:cue for cue in cues};fixed=[]
    for item in proposal.actions:
        value=item.model_dump(mode='json');cue=by_id.get(value['cue_id']);text=cue.text if cue else ''
        if item.due_on is not None:
            value['due_quote']=quoted_dates(text).get(item.due_on)
            if value['due_quote'] is None:value['due_on']=None
        else:
            deadlines=quoted_dates(item.quote,deadline=True) if item.quote in text else {}
            value['due_quote']=None
            if len(deadlines)==1:
                due,quote=next(iter(deadlines.items()))
                value.update(due_on=due,due_quote=quote)
        if cue and value['owner']!='unassigned' and value['owner']!=cue.speaker:value['owner']='unassigned'
        if value['commitment']=='conditional' and not (value['condition'] and value['condition'].strip() and value['condition'] in text):
            value['commitment']='tentative';value['condition']=None
        if value['commitment']=='agreed' and HEDGES.search(value['quote'] or ''):value['commitment']='tentative'
        if value['commitment']=='agreed' and value['owner']=='unassigned':value['commitment']='unclear'
        fixed.append(adapter.validate_python(value))
    return proposal.model_copy(update={'actions':tuple(fixed)})


async def propose(context: GenerationContext) -> Piece:
    prompt=load(MINUTES_WRITER.prompt,PROMPT);system=prompt['system']
    payload=json.dumps({'source_kind':context.source_kind,'review_note':context.review_note,'cues':[cue.model_dump(mode='json') for cue in context.cues]},ensure_ascii=False)
    with span('meeting.agent','agent',agent=MINUTES_WRITER.name,intent='draft_minutes',side='meeting',path='workflow',prompt_version=prompt['id']):
        result=await model_step(prompt,system,payload,MinutesProposal.model_json_schema())
        proposal=repair_actions(MinutesProposal.model_validate(result.data),context.cues)
        content=bind_action_ids(proposal,context.source_id)
    charged=(result.input_tokens+result.output_tokens) if result.input_tokens is not None and result.output_tokens is not None else estimated_charge(system,payload,MinutesProposal.model_json_schema())
    return Piece(content,result.input_tokens,result.output_tokens,max(1,charged))


def distinct_claims(items: tuple[EvidenceClaim,...]) -> tuple[EvidenceClaim,...]:
    result: list[EvidenceClaim]=[];seen: set[str]=set()
    for item in items:
        key=json.dumps(item.model_dump(mode='json'),sort_keys=True)
        if key not in seen:result.append(item);seen.add(key)
    return tuple(result)


def distinct_actions(pieces: tuple[MinutesContent,...]) -> tuple[ProposedAction,...]:
    result: list[ProposedAction]=[];seen: set[str]=set()
    from pydantic import TypeAdapter
    adapter=TypeAdapter(ProposedAction)
    for piece in pieces:
        for item in piece.actions:
            value=item.model_dump(mode='json',exclude={'action_id'})
            key=json.dumps({name:value[name] for name in ('text','owner','commitment','condition','due_on')},sort_keys=True)
            if key not in seen:result.append(adapter.validate_python(value));seen.add(key)
    return tuple(result)


def claim_groups(summaries: tuple[EvidenceClaim,...]) -> tuple[tuple[int,...],...]:
    """Group candidates supported by the same quoted statement, including repeated speech across chunks."""
    groups: dict[str,list[int]]={}
    for index,item in enumerate(summaries):groups.setdefault(' '.join(item.quote.split()),[]).append(index)
    return tuple(tuple(indices) for indices in groups.values())


def selected_summaries(summaries: tuple[EvidenceClaim,...],groups: tuple[tuple[int,...],...],selection: MinutesSelection) -> tuple[EvidenceClaim,...]:
    indices=selection.summary_indices
    if any(index<0 or index>=len(summaries) for index in indices):raise ValueError('Minutes overview selected an unknown summary index')
    selected_groups=tuple(group for group in groups if any(index in group for index in indices))
    if len(selected_groups)!=len(indices):raise ValueError('Minutes overview repeats a supporting statement or selects one already covered by a decision')
    if len(groups)<=4 and len(selected_groups)!=len(groups):raise ValueError('Minutes overview omitted a distinct supporting statement that fits within the four-item limit')
    return tuple(summaries[index] for index in indices)


def selected_decisions(decisions: tuple[EvidenceClaim,...],groups: tuple[tuple[int,...],...],selection: MinutesSelection) -> tuple[EvidenceClaim,...]:
    indices=selection.decision_indices
    if any(index<0 or index>=len(decisions) for index in indices):raise ValueError('Minutes overview selected an unknown decision index')
    if len(indices)!=len(groups) or any(sum(index in group for index in indices)!=1 for group in groups):
        raise ValueError('Minutes overview must retain one decision per distinct supporting statement without repetitions')
    return tuple(decisions[index] for index in indices)


async def merge(pieces: tuple[MinutesContent,...],context: GenerationContext) -> Piece:
    summaries=distinct_claims(tuple(item for piece in pieces for item in piece.summary));title=pieces[0].title
    decisions=distinct_claims(tuple(item for piece in pieces for item in piece.decisions))
    charged=0;input_tokens=None;output_tokens=None
    if len(pieces)>1:
        prompt=load(MINUTES_OVERVIEW.prompt,MINUTES_OVERVIEW.version);groups=claim_groups(summaries);decision_groups=claim_groups(decisions)
        if len(decision_groups)>10:raise ValueError('The complete meeting exceeds the ten distinct supported decisions allowed for RM approval')
        decision_quotes={' '.join(item.quote.split()) for item in decisions}
        uncovered=tuple(group for group in groups if ' '.join(summaries[group[0]].quote.split()) not in decision_quotes)
        if uncovered:groups=uncovered
        payload=json.dumps({'review_note':context.review_note,'summaries':[{'index':index,'text':item.text,'quote':item.quote,'cue_id':item.cue_id} for index,item in enumerate(summaries)],
            'summary_groups':groups,'decision_groups':decision_groups,
            'decisions':[{'index':index,**item.model_dump(mode='json')} for index,item in enumerate(decisions)]},ensure_ascii=False)
        schema=MinutesSelection.model_json_schema()
        if len(groups)<=4:schema['properties']['summary_indices']['minItems']=len(groups)
        schema['properties']['decision_indices'].update(minItems=len(decision_groups),maxItems=len(decision_groups))
        with span('meeting.agent','agent',agent=MINUTES_OVERVIEW.name,intent='consolidate_minutes',side='meeting',path='workflow',prompt_version=prompt['id']):
            result=await model_step(prompt,prompt['system'],payload,schema)
            input_tokens=result.input_tokens;output_tokens=result.output_tokens
            charged=(input_tokens+output_tokens) if input_tokens is not None and output_tokens is not None else estimated_charge(prompt['system'],payload,schema)
            selected=MinutesSelection.model_validate(result.data)
            summaries=selected_summaries(summaries,groups,selected);decisions=selected_decisions(decisions,decision_groups,selected);title=selected.title
    proposal=MinutesProposal(title=title,summary=summaries,decisions=decisions,actions=distinct_actions(pieces))
    if sum(len(item.text) for item in proposal.summary)+len(proposal.summary)-1>2000:raise ValueError('The complete-call summary exceeds the RM note limit of 2000 characters')
    return Piece(bind_action_ids(proposal,context.source_id),input_tokens,output_tokens,charged)


async def generate(context: GenerationContext) -> MinutesContent:
    pieces: list[MinutesContent]=[];spent=0
    for selected in chunks(context.cues):
        piece=await propose(context.model_copy(update={'cues':selected}))
        pieces.append(piece.content);spent+=piece.charged_tokens
        if spent>settings.meeting_model_token_budget:
            from app.services.llm_gateway import LLMBudgetExceeded
            raise LLMBudgetExceeded('Meeting model token budget exceeded; no partial draft was published')
    merged=await merge(tuple(pieces),context)
    if spent+merged.charged_tokens>settings.meeting_model_token_budget:
        from app.services.llm_gateway import LLMBudgetExceeded
        raise LLMBudgetExceeded('Complete meeting model token budget exceeded during overview selection')
    return merged.content
