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
from app.services.privacy import redact

JUDGE_PROMPT=Path(__file__).parent/'judge'/'v4.md'
JUDGE_SCHEMA={'title':'judge_grade','type':'object','additionalProperties':False,'required':['score','reason'],
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


def counts_match(got: dict[str,int],want: dict[str,int|str]) -> bool:
    """Agent run counts: exact, or a range "a-b" (a fan-out whose size depends on the records, R21)."""
    def fits(n: int,w: int|str) -> bool:
        if isinstance(w,str) and re.fullmatch(r'\d+-\d+',w):lo,hi=map(int,w.split('-'));return lo<=n<=hi
        return n==w
    return all(fits(got.get(a,0),want.get(a,0)) for a in {*got,*want})


def trajectory_checks(t: dict[str,Any],out: dict[str,Any]) -> list[dict[str,Any]]:
    found=[]
    if t.get('pipeline')=='rm':found.append(result('nodes',not out['nodes']['ran'],'RM uses /rm/assistant, not the client LangGraph'))
    elif t.get('graph') is False:found.append(result('nodes',not out['nodes']['ran'],'graph must not run; ran: '+', '.join(out['nodes']['ran'])))
    else:
        acted=set(out['nodes']['acted'])
        missing,extra=[n for n in t['nodes'] if n not in acted],[n for n in t['idle'] if n in acted]
        found.append(result('nodes',not missing and not extra,'; '.join(filter(None,[missing and 'did not act: '+', '.join(missing),extra and 'should be idle: '+', '.join(extra)]))))
    if t.get('agents') or t.get('graph') is False:
        found.append(result('agents',counts_match(out['agents'],t['agents']),f"ran {out['agents']}, expected {t['agents']}"))
    called=out['tools']
    if t.get('tools') or t.get('no_tools') or t.get('graph') is False:
        missing=[x for x in t.get('tools',[]) if x not in called]
        banned=[x for x in called if any(fnmatch(x,p) for p in t.get('no_tools',[]))]+(called if t.get('graph') is False else [])
        found.append(result('tools',not missing and not banned,'; '.join(filter(None,[missing and 'not called: '+', '.join(missing),banned and 'must not call: '+', '.join(banned)]))))
    if 'model' in t:found.append(result('model',bool(out['model_calls'])==t['model'],f"{out['model_calls']} model calls"))
    if t.get('service_reads'):
        missing=[name for name in t['service_reads'] if name not in out.get('service_reads',[])]
        found.append(result('service_reads',not missing,'not read: '+', '.join(missing) if missing else ''))
    if deps:=out.get('dependencies'):found+=dependency_checks(t,deps)
    return found


def dependency_checks(t: dict[str,Any],deps: dict[str,Any]) -> list[dict[str,Any]]:
    """v5 agent interactions (phase 7): waves, exports passed (names only), skipped dependants and the one handoff."""
    found=[]
    if 'waves' in t:found.append(result('waves',deps['waves']==t['waves'],f"{deps['waves']} waves"))
    if t.get('exports'):
        missing=[f"{e['from']}>{e['to']}:{e['name']}" for e in t['exports'] if f"{e['from']}>{e['to']}:{e['name']}" not in deps['exports']]
        found.append(result('exports',not missing,'not passed: '+', '.join(missing) if missing else ', '.join(deps['exports'])))
    if 'skipped' in t:found.append(result('skipped',deps['skipped']==t['skipped'],f"skipped {deps['skipped']}"))
    if 'rerouted' in t:found.append(result('rerouted',deps['rerouted']==t['rerouted'],f"rerouted {deps['rerouted']}"))
    return found


def plan_checks(out: dict[str,Any],ref: dict[str,Any]) -> list[dict[str,Any]]:
    """The planner-only dataset: the route, the agents, each expected dependency (upstream agent and condition) and priority."""
    got=out.get('plan')
    if got is None:return [result('played',None,'no plan: '+json.dumps(out.get('skipped')))]
    found=[result('route',got['route']==ref.get('route','plan'),f"route {got['route']}")]
    if ref.get('route') not in (None,'plan'):return found
    expected=Counter(t['agent'] for t in ref['tasks']);planned=Counter(t['agent'] for t in got['tasks'])
    found.append(result('agents',planned==expected,f'planned {dict(planned)}, expected {dict(expected)}'))
    named={t['id']:t['agent'] for t in ref['tasks'] if 'id' in t}
    links=[(t['agent'],named[t['depends_on']],t.get('condition')) for t in ref['tasks'] if 'depends_on' in t]
    made={(t['agent'],t['depends_on'],t['condition']) for t in got['tasks'] if t['depends_on']}
    if links:
        missing=[f'{a} after {u}'+(f' if {c}' if c else '') for a,u,c in links if not any(m[0]==a and m[1]==u and (c is None or m[2]==c) for m in made)]
        found.append(result('dependencies',not missing,'missing: '+', '.join(missing) if missing else f'{len(made)} linked'))
    if ref.get('no_dependencies'):found.append(result('dependencies',not made,f'{len(made)} linked'))
    if ref.get('no_agents'):found.append(result('no_agents',not set(ref['no_agents'])&set(planned),f'planned {dict(planned)}'))
    wrong=[t['agent'] for t in ref['tasks'] if 'priority' in t and not any(g['agent']==t['agent'] and g['priority']==t['priority'] for g in got['tasks'])]
    if any('priority' in t for t in ref['tasks']):found.append(result('priority',not wrong,'wrong: '+', '.join(wrong) if wrong else ''))
    return found


def checks(out: dict[str,Any],ref: dict[str,Any]) -> list[dict[str,Any]]:
    """out: the target's output. ref: the example's reference outputs."""
    if 'plan' in ref:return plan_checks(out,ref['plan'])
    if not out['played']:return [result('played',None,'no step can be played in chat: '+json.dumps(out['skipped']))]
    first=out['replies'][0]
    errors=[r['error'] for r in out['replies'] if 'error' in r]
    found=[result('completed',not errors,', '.join(map(str,errors)) or f"{len(out['replies'])} turns")]
    # A button the case taps but the previous reply never offered: the conversation stopped where the browser would have.
    if out.get('not_offered'):found.append(result('offered',False,'not offered: '+', '.join(s.get('tap','RM handover confirmation') for s in out['not_offered'])))
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
    if ref.get('source_prefix'):
        cited={s for r in out['replies'] for s in r.get('sources',[])}
        found.append(result('record_sources',any(s.startswith(ref['source_prefix']) for s in cited),'cited '+(', '.join(sorted(cited)) or 'nothing')))
    if 'expected_origin' in ref:found.append(result('origin',first.get('origin')==ref['expected_origin'],f"first turn origin {first.get('origin')}"))
    if 'expected_route' in ref:found.append(result('fixed_route',out['routes'][0]==ref['expected_route'],f"route {out['routes'][0]}"))
    if 'expect_needs_human' in ref:found.append(result('needs_human',first.get('needs_human')==ref['expect_needs_human'],f"needs_human {first.get('needs_human')}"))
    if 'ticket' in ref:found.append(ticket_check(ref['ticket'],out))
    for category in ref.get('ticket_categories',[]):
        checked=ticket_check(category,out)
        found.append({**checked,'key':'ticket_'+category})
    # A graph case also carries the v4 expectation it was written against; only the v5 one describes the running graph.
    if t:=ref.get('trajectory_v5') or ref.get('trajectory'):found+=trajectory_checks(t,out)
    return found


def deterministic(run,example) -> dict[str,list[dict[str,Any]]]:
    """LangSmith evaluator: one feedback per applicable check."""
    return {'results':checks(run.outputs or {},example.outputs or {})}


def judge_name() -> str:
    """Require an explicit judge distinct from the actual answering model on either transport."""
    name=os.getenv('JUDGE_MODEL')
    if not name:raise RuntimeError('Set JUDGE_MODEL to use --judge')
    from app.services.llm_gateway import model_name
    if name==model_name():raise RuntimeError('JUDGE_MODEL must differ from the answering model')
    return name


def judge_model():
    name=judge_name()
    from langchain.chat_models import init_chat_model
    provider=os.getenv('LLM_PROVIDER') or 'openai'
    kwargs={'api_key':os.environ['LLM_API_KEY'],'base_url':os.environ['LLM_BASE_URL']} if provider=='openai' else {}
    return init_chat_model(name,model_provider=provider,timeout=60,max_retries=1,**kwargs).with_structured_output(JUDGE_SCHEMA,method='json_schema',strict=True)


def transcript(out: dict[str,Any]) -> str:
    role='Relationship manager' if out.get('pipeline')=='rm' else 'Client'
    return redact('\n\n'.join(f"{role}: {r['say']}\nAssistant: {r.get('answer') or '(no reply: '+str(r.get('error'))+')'}\nSources: {', '.join(r.get('sources',[])) or 'none'}\nSource records: {json.dumps(r.get('evidence',[]),ensure_ascii=False)}"
        for r in out['replies']))[0]


async def judge(run,example) -> dict[str,Any]:
    """LangSmith evaluator (opt-in): model-graded `check`, labelled as such in the key."""
    out,ref=run.outputs or {},example.outputs or {}
    if not out.get('played'):return result('judge_rubric',None,'nothing played')
    case=redact('\n'.join([f"Expected: {ref['check']}",*('Must not: '+x for x in ref.get('must_not',[]))]))[0]
    system=JUDGE_PROMPT.read_text(encoding='utf-8')
    quoted=set(re.findall(r'TCK-\d+',out.get('answer','')))
    tickets=out.get('tickets',{})
    relevant={'opened':tickets.get('opened',[]),'quoted':[t for t in tickets.get('all',[]) if t['reference'] in quoted]}
    message=redact(f'{case}\n\nAuthorised RM book: {json.dumps(out.get("authorised_book"),ensure_ascii=False)}\n\nRecorded ticket reads: {json.dumps(relevant,ensure_ascii=False)}\n\nUnplayed steps: {json.dumps(out.get("skipped",[]),ensure_ascii=False)}\n\nConversation:\n{transcript(out)}')[0]
    from app.services.llm_gateway import transport,check
    if transport()=='codex':
        from app.services import codex_cli
        from app.core.observability import span
        name=judge_name()
        with span('judge','llm',agent='judge',transport='codex',model=name,prompt_version='judge/'+JUDGE_PROMPT.stem,
                model_inputs={'system':system,'messages':[{'role':'user','content':message}]}) as metadata:
            graded,usage=await codex_cli.answer_with_model(system,JUDGE_SCHEMA,[('user',message)],name)
            metadata.update(usage)
            metadata['model_outputs']=graded
    else:graded=await judge_model().ainvoke([('system',system),('user',message)])
    check(graded,JUDGE_SCHEMA)
    return {'key':'judge_rubric','score':graded['score'],'comment':graded['reason'][:1000]}
