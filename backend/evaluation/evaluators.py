"""Evaluators (docs/evaluation/README.md §5) for the target in targets.py.

checks() scores what can be decided from the run without a model: S (reply_has/reply_lacks, sources, origin, fixed
route, needs_human, ticket) and T (nodes, agents, tools, model). It returns only the checks a case defines; a check
that depends on a step the chat cannot play scores None with the reason. judge() is the opt-in C check: a different
model grades the conversation against the case's `check` and `must_not`. It is an aid to review, not proof."""
import json
import os
import re
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

JUDGE_PROMPT=Path(__file__).parent/'judge'/'v1.md'
JUDGE_SCHEMA={'type':'object','additionalProperties':False,'required':['score','reason'],
    'properties':{'score':{'type':'number','enum':[0,0.5,1]},'reason':{'type':'string'}}}


def result(key: str,ok: bool | None,comment: str='') -> dict[str,Any]:
    return {'key':key,'score':None if ok is None else int(ok),'comment':comment}


def ticket_check(expected: str | None,out: dict[str,Any]) -> dict[str,Any]:
    opened=out['tickets']['opened']
    if expected is None:return result('ticket',not opened,', '.join(f"{t['reference']} {t['category']}" for t in opened) or 'none opened')
    if out['skipped']:return result('ticket',None,'opened by a step the chat runner cannot play')
    quoted=set(re.findall(r'TCK-\d+',out['answer']))
    # A reused open ticket (same topic) is quoted but not new; either way it must be the expected category.
    match=[t for t in out['tickets']['all'] if t['category']==expected and (t['reference'] in quoted or t in opened)]
    return result('ticket',bool(match),', '.join(t['reference'] for t in match) or f'no {expected} ticket opened or quoted')


def trajectory_checks(t: dict[str,Any],out: dict[str,Any]) -> list[dict[str,Any]]:
    found=[]
    if t.get('graph') is False:found.append(result('nodes',not out['nodes']['ran'],'graph must not run; ran: '+', '.join(out['nodes']['ran'])))
    else:
        acted=set(out['nodes']['acted'])
        missing,extra=[n for n in t['nodes'] if n not in acted],[n for n in t['idle'] if n in acted]
        found.append(result('nodes',not missing and not extra,'; '.join(filter(None,[missing and 'did not act: '+', '.join(missing),extra and 'should be idle: '+', '.join(extra)]))))
    if t['agents'] or t.get('graph') is False:
        found.append(result('agents',Counter(out['agents'])==Counter(t['agents']),f"ran {out['agents']}, expected {t['agents']}"))
    called=out['tools']
    if t['tools'] or t.get('no_tools') or t.get('graph') is False:
        missing=[x for x in t['tools'] if x not in called]
        banned=[x for x in called if any(fnmatch(x,p) for p in t.get('no_tools',[]))]+(called if t.get('graph') is False else [])
        found.append(result('tools',not missing and not banned,'; '.join(filter(None,[missing and 'not called: '+', '.join(missing),banned and 'must not call: '+', '.join(banned)]))))
    if 'model' in t:found.append(result('model',bool(out['model_calls'])==t['model'],f"{out['model_calls']} model calls"))
    return found


def checks(out: dict[str,Any],ref: dict[str,Any]) -> list[dict[str,Any]]:
    """out: the target's output. ref: the example's reference outputs."""
    if not out['played']:return [result('played',None,'no step can be played in chat: '+json.dumps(out['skipped']))]
    first=out['replies'][0]
    errors=[r['error'] for r in out['replies'] if 'error' in r]
    found=[result('completed',not errors,', '.join(map(str,errors)) or f"{len(out['replies'])} turns")]
    # A button the case taps but the previous reply never offered: the conversation stopped where the browser would have.
    if out.get('not_offered'):found.append(result('offered',False,'not offered: '+', '.join(s['tap'] for s in out['not_offered'])))
    text=out['answer']
    if ref.get('reply_has'):
        missing=[p for p in ref['reply_has'] if not re.search(p,text)]
        found.append(result('reply_has',not missing,'missing /'+'/, /'.join(missing)+'/' if missing else ''))
    if ref.get('reply_lacks'):
        present=[p for p in ref['reply_lacks'] if re.search(p,text)]
        found.append(result('reply_lacks',not present,'found /'+'/, /'.join(present)+'/' if present else ''))
    if ref.get('cites_any'):
        cited={s for r in out['replies'] for s in r.get('sources',[])}
        found.append(result('cites',bool(cited&set(ref['cites_any'])),'cited '+(', '.join(sorted(cited)) or 'nothing')))
    if 'expected_origin' in ref:found.append(result('origin',first.get('origin')==ref['expected_origin'],f"first turn origin {first.get('origin')}"))
    if 'expected_route' in ref:found.append(result('fixed_route',out['routes'][0]==ref['expected_route'],f"route {out['routes'][0]}"))
    if 'expect_needs_human' in ref:found.append(result('needs_human',first.get('needs_human')==ref['expect_needs_human'],f"needs_human {first.get('needs_human')}"))
    if 'ticket' in ref:found.append(ticket_check(ref['ticket'],out))
    if 'trajectory' in ref:found+=trajectory_checks(ref['trajectory'],out)
    return found


def deterministic(run,example) -> dict[str,list[dict[str,Any]]]:
    """LangSmith evaluator: one feedback per applicable check."""
    return {'results':checks(run.outputs or {},example.outputs or {})}


def judge_model():
    """The judge must be configured and must not be the answering model."""
    name=os.getenv('JUDGE_MODEL')
    if not name:raise RuntimeError('Set JUDGE_MODEL to use --judge')
    if name==os.getenv('LLM_MODEL'):raise RuntimeError('JUDGE_MODEL must differ from LLM_MODEL')
    from langchain.chat_models import init_chat_model
    provider=os.getenv('LLM_PROVIDER') or 'openai'
    kwargs={'api_key':os.environ['LLM_API_KEY'],'base_url':os.environ['LLM_BASE_URL']} if provider=='openai' else {}
    return init_chat_model(name,model_provider=provider,timeout=60,max_retries=1,**kwargs).with_structured_output(JUDGE_SCHEMA,method='json_schema',strict=True)


def transcript(out: dict[str,Any]) -> str:
    return '\n\n'.join(f"Client: {r['say']}\nAssistant: {r.get('answer') or '(no reply: '+str(r.get('error'))+')'}\nSources: {', '.join(r.get('sources',[])) or 'none'}"
        for r in out['replies'])


async def judge(run,example) -> dict[str,Any]:
    """LangSmith evaluator (opt-in): model-graded `check`, labelled as such in the key."""
    out,ref=run.outputs or {},example.outputs or {}
    if not out.get('played'):return result('judge_rubric',None,'nothing played')
    case='\n'.join([f"Expected: {ref['check']}",*('Must not: '+x for x in ref.get('must_not',[]))])
    graded=await judge_model().ainvoke([('system',JUDGE_PROMPT.read_text(encoding='utf-8')),('user',f'{case}\n\nConversation:\n{transcript(out)}')])
    return {'key':'judge_rubric','score':graded['score'],'comment':graded['reason'][:1000]}
