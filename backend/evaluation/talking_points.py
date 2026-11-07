"""Talking points evaluation (0108, build plan phase 4; lane 1 v2 since 0143): precision and recall per topic for lane 1
on hand-labelled messages, and the final state of hand-labelled conversations.

Offline (default): python -m evaluation.talking_points   (code only: care, the pre-filter and lane 2's rule; no model calls)
Live (opt-in):     python -m evaluation.talking_points --live --output talking-points-results.json
Version:           --version v1|v2 (default: prompts.VERSIONS['talking_points'], the version in use)
Conversations:     python -m evaluation.talking_points --conversations [--live] [--version v1|v2]

Each single-message case runs the steps `talking_points.after_reply` runs, without a database: the guardrail on her
(redacted) history, the care pause, the code pre-filter, then (live only) the real model call through `llm_gateway.call`
(TALKING_POINTS_MODEL, else LLM_MODEL; no retry) and the code validators. v2 runs each case with an empty state (no points,
no active topic; her previous message as the earlier message). A case's prediction is the set of topics a point would be
recorded for. Offline, a case the pre-filter passes has no prediction, so only the pre-filter is scored (positives it lets
through, negatives it stops before a model call). Lane 2 (`share_candidate`, a rule) is scored in both modes: a
Let-your-RM-know offer on a case labelled with no topic is a false prompt.

Cases in talking_points_cases.json are synthetic and labelled by hand: `expect` maps each topic that should be recorded
to its stage (empty: nothing should be stored). Groups: positive, multi, previous (her earlier message as context),
negative, sensitive, care and injection.

Conversation cases (talking_points_conversations.json) are a starting state (`start`, `muted`), her messages in one or
more chats (`chats`: a new chat starts with no active topic) and the expected final state: `expect` per topic (stage,
timeframe, kind, on_hold, concerns_has), `unchanged_from` n (the final state equals the state after her first n
messages; 0 is the start), `paused` and `allow_on_hold`. Each turn runs as `after_reply` does (guardrail, care, the gate,
the model, the validators) and applies the kept changes in memory with `talking_points_state.apply` (the SQL's rules),
the carry-over window of 4 turns per chat included. v1 runs the same turns as v1 does (her previous message only, every
point an add). Scored: final-state match per case, false points (a topic she never planned), false on hold (an on hold
on a topic not in `allow_on_hold`; must be 0), and model calls per conversation. A live run is one sample of a
non-deterministic model: review the misses."""
import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from app.core.config import settings
from app.core.observability import start_trace
from app.schemas.chat import ChatRequest
from app.schemas.talking_points_state import TopicState
from app.services import llm_gateway,prompts,talking_points as tp,talking_points_state as v2
from app.services.guardrail import assess_request

HERE=Path(__file__).parent
CASES=HERE/'talking_points_cases.json'
CONVERSATIONS=HERE/'talking_points_conversations.json'
REPLY='Here is what I found.'
WINDOW=4  # = insights.active_topics: topics recorded from her last 4 turns in the same chat


def cases(path: Path=CASES) -> list[dict]:
    return json.loads(path.read_text(encoding='utf-8'))


def assess(history: list[str]):
    """The guardrail on her messages in one chat, each followed by a placeholder reply."""
    messages=[item for text in history for item in ({'role':'user','content':text},{'role':'assistant','content':REPLY})][:-1]
    return assess_request(ChatRequest(messages=messages,mode='personal'))


def assessed(case: dict):
    return assess([*([case['previous']] if case.get('previous') else []),case['message']])


def empty_state(points: dict[str,dict] | None=None,active=(),muted=(),paused: bool=False) -> TopicState:
    return TopicState(chat_topics=True,paused=paused,has_rm=True,rm_first_name='Sophie',muted=list(muted),prompted=[],meeting_topics=[],
        points=list((points or {}).values()),active_topics=list(active))


async def model(version: str,message: str,previous: list[str],state: TopicState) -> tuple[list[dict],dict]:
    """One live classification of her latest message: the kept changes (v1: adds) and what was proposed and dropped."""
    prompt=prompts.load('talking_points',version)
    start_trace()
    if version=='v1':
        result=await llm_gateway.call(prompt,tp.system_text(prompt),tp.model_input(message,previous[-1] if previous else None),tp.SCHEMA,
            model=settings.talking_points_model or None,retries=0)
        kept,dropped=tp.validate(result.data.get('points') or [],message,set(state.muted))
        return [{**p,'action':'add','kind':None,'carried':False} for p in kept],{'proposed':len(result.data.get('points') or []),'dropped':dict(dropped)}
    result=await llm_gateway.call(prompt,prompt['system'],v2.model_input(state,previous[-v2.EARLIER:],message),v2.SCHEMA,
        model=settings.talking_points_model or None,retries=0)
    kept,dropped=v2.validate(result.data.get('changes') or [],message,state)
    return kept,{'proposed':len(result.data.get('changes') or []),'dropped':dict(dropped)}


async def classify(case: dict,live: bool,version: str='v1') -> dict:
    """What lane 1 would record for this case: {'gate', 'topics' (None offline when the model would be asked), 'stages', 'dropped'}."""
    risk=assessed(case)
    if risk.care:return {'gate':'care','topics':[],'stages':{},'dropped':{}}
    reason=tp.prefilter(risk.redacted_message,risk)
    if reason:return {'gate':reason,'topics':[],'stages':{},'dropped':{}}
    if not live:return {'gate':'model','topics':None,'stages':{},'dropped':{}}
    try:kept,seen=await model(version,risk.redacted_message,[case['previous']] if case.get('previous') else [],empty_state())
    except llm_gateway.LLMError as error:
        return {'gate':'model','topics':[],'stages':{},'dropped':{},'error':error.reason}
    adds=[p for p in kept if p['action']=='add']  # an empty state: an update or on hold has no point to change
    return {'gate':'model','topics':[p['topic'] for p in adds],'stages':{p['topic']:p['stage'] for p in adds},'dropped':seen['dropped'],
        'quotes':[p['quote'] for p in adds],'proposed':seen['proposed']}


def lane_two(case: dict) -> str | None:
    """The topic a Let-your-RM-know offer would name (the code rule, before her status is read), or None."""
    risk=assessed(case)
    if risk.care or risk.fixed_response is not None or risk.risk_flags:return None
    found=tp.share_candidate(risk.redacted_message)
    return found.topic if found else None


def score(rows: list[dict]) -> dict:
    per={topic:Counter() for topic in tp.TOPICS}
    stages=Counter()
    for row in rows:
        if row['topics'] is None:continue
        expected,predicted=set(row['expect']),set(row['topics'])
        for topic in tp.TOPICS:
            if topic in expected and topic in predicted:
                per[topic]['tp']+=1
                stages['same' if row['stages'].get(topic)==row['expect'][topic] else 'different']+=1
            elif topic in predicted:per[topic]['fp']+=1
            elif topic in expected:per[topic]['fn']+=1
    def ratio(a,b):return round(a/b,3) if b else None
    topics={t:{'tp':c['tp'],'fp':c['fp'],'fn':c['fn'],'precision':ratio(c['tp'],c['tp']+c['fp']),'recall':ratio(c['tp'],c['tp']+c['fn'])} for t,c in per.items()}
    total=Counter()
    for c in per.values():total.update(c)
    return {'topics':topics,'micro':{'precision':ratio(total['tp'],total['tp']+total['fp']),'recall':ratio(total['tp'],total['tp']+total['fn'])},
        'stage_agreement':ratio(stages['same'],stages['same']+stages['different']),'false_points':total['fp']}


async def run(selected: list[dict],live: bool,version: str='v1') -> dict:
    rows=[]
    for case in selected:
        row={'id':case['id'],'group':case['group'],'expect':case['expect'],**await classify(case,live,version),'share':lane_two(case)}
        rows.append(row)
    gates=Counter(r['gate'] for r in rows)
    labelled=[r for r in rows if r['expect']]
    report={'mode':'live' if live else 'offline','version':version,'cases':len(rows),'dataset_sha256':hashlib.sha256(CASES.read_bytes()).hexdigest(),
        'prompt_version':prompts.load('talking_points',version)['id'],'model':llm_gateway.model_name(settings.talking_points_model or None) if live else None,
        'gates':dict(gates),'model_calls':gates['model'],
        'prefilter':{'positives_passed':sum(r['gate']=='model' for r in labelled),'positives':len(labelled),
            'negatives_stopped':sum(r['gate']!='model' for r in rows if not r['expect']),'negatives':sum(not r['expect'] for r in rows)},
        'lane_two':{'offers':sum(r['share'] is not None for r in rows),'false_prompts':[r['id'] for r in rows if r['share'] and r['share'] not in r['expect']],
            'by_group':dict(Counter(r['group'] for r in rows if r['share']))},
        'errors':[r['id'] for r in rows if r.get('error')],'rows':rows}
    if live:
        report.update(score(rows))
        report['misses']=[{'id':r['id'],'expect':sorted(r['expect']),'got':sorted(r['topics']),'dropped':r['dropped']} for r in rows
            if set(r['topics'] or [])!=set(r['expect'])]
        report['by_group']={g:{'cases':sum(r['group']==g for r in rows),'exact':sum(r['group']==g and set(r['topics'])==set(r['expect']) for r in rows)}
            for g in dict.fromkeys(r['group'] for r in rows)}
    return report


# Conversations ------------------------------------------------------------------------------------------------------
def conversations(path: Path=CONVERSATIONS) -> list[dict]:
    return json.loads(path.read_text(encoding='utf-8'))


async def replay(case: dict,live: bool,version: str) -> dict:
    """Her messages in order, as after_reply runs them, with the state kept in memory: the final state, what each turn did
    (gate, model call, applied changes) and every on hold applied."""
    points={p['topic']:dict(p) for p in case.get('start',[])}
    muted,paused,snapshots,turns,holds,errors=list(case.get('muted',[])),False,[json.loads(json.dumps(points))],[],[],[]
    for chat_no,chat in enumerate(case['chats']):
        said,recorded=[],[]  # her messages in this chat; the topics recorded on each of her turns in it
        for text in chat:
            risk=assess([*said,text])
            message=risk.redacted_message or text
            active=sorted({t for topics in recorded[-WINDOW:] for t in topics if t in points}) if version=='v2' else []
            step={'chat':chat_no,'gate':None,'called':False,'applied':[],'skipped':[]}
            if risk.care:paused,step['gate']=True,'care'
            elif paused:step['gate']='paused'
            else:step['gate']=(v2.gate(message,risk,active=active) if version=='v2' else tp.prefilter(message,risk)) or 'model'
            topics=set()
            if step['gate']=='model' and live:
                step['called']=True
                try:kept,seen=await model(version,message,said,empty_state(points,active,muted))
                except llm_gateway.LLMError as error:kept,seen=[],{'error':error.reason};errors.append(error.reason)
                step.update(proposed=seen.get('proposed'),dropped=seen.get('dropped'))
                for change in kept:
                    points,reason=v2.apply(points,change,active,muted)
                    if reason:step['skipped'].append({'topic':change['topic'],'reason':reason});continue
                    topics.add(change['topic'])
                    step['applied'].append({k:change[k] for k in ('action','topic','stage','timeframe','kind','concerns','carried')})
                    if change['action']=='on_hold':holds.append(change['topic'])
            elif step['gate']=='model':step['called']=True
            recorded.append(topics)
            said.append(message)
            turns.append(step)
            snapshots.append(json.loads(json.dumps(points)))
    return {'final':points,'paused':paused,'turns':turns,'holds':holds,'snapshots':snapshots,'errors':errors}


def judge(case: dict,result: dict) -> dict:
    """Final-state match, false points and false on holds for one conversation."""
    final,problems=result['final'],[]
    started={p['topic'] for p in case.get('start',[])}
    expected=case.get('expect',{})
    false_points=sorted(set(final)-started-set(expected))
    for topic,fields in expected.items():
        point=final.get(topic)
        if point is None:problems.append(f'{topic}: missing');continue
        for field,value in fields.items():
            if field=='concerns_has':
                if not set(value)<=set(point['concerns']):problems.append(f'{topic}.concerns: {point["concerns"]}')
            elif point.get(field)!=value:problems.append(f'{topic}.{field}: {point.get(field)!r}')
    if 'unchanged_from' in case and final!=result['snapshots'][case['unchanged_from']]:problems.append('changed')
    if case.get('paused') and not result['paused']:problems.append('not paused')
    false_on_hold=sorted({t for t in result['holds'] if t not in case.get('allow_on_hold',[])})
    return {'match':not problems and not false_points and not false_on_hold,'problems':problems,'false_points':false_points,'false_on_hold':false_on_hold}


async def run_conversations(selected: list[dict],live: bool,version: str) -> dict:
    rows=[]
    for case in selected:
        result=await replay(case,live,version)
        rows.append({'id':case['id'],'note':case.get('note'),**judge(case,result),'calls':sum(t['called'] for t in result['turns']),
            'final':result['final'],'turns':result['turns'],'errors':result['errors']})
    calls=[r['calls'] for r in rows]
    report={'mode':'live' if live else 'offline','version':version,'cases':len(rows),
        'dataset_sha256':hashlib.sha256(CONVERSATIONS.read_bytes()).hexdigest(),'prompt_version':prompts.load('talking_points',version)['id'],
        'model':llm_gateway.model_name(settings.talking_points_model or None) if live else None,
        'model_calls':sum(calls),'calls_per_conversation':round(sum(calls)/len(calls),2) if calls else None,
        'errors':[r['id'] for r in rows if r['errors']],'rows':rows}
    if live:
        report.update(final_state_match=sum(r['match'] for r in rows),false_points=[f"{r['id']}:{t}" for r in rows for t in r['false_points']],
            false_on_hold=[f"{r['id']}:{t}" for r in rows for t in r['false_on_hold']],
            misses=[{'id':r['id'],'problems':r['problems'],'false_points':r['false_points'],'false_on_hold':r['false_on_hold']} for r in rows if not r['match']])
    return report


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live',action='store_true',help='Call the real model for each case the pre-filter passes')
    parser.add_argument('--version',choices=('v1','v2'),default=None,help='Lane 1 version (default: the configured one)')
    parser.add_argument('--conversations',action='store_true',help='Run the conversation cases instead of the single messages')
    parser.add_argument('--only',help='Comma list of case ids')
    parser.add_argument('--output',type=Path,help='Write the full report as JSON')
    args=parser.parse_args()
    version=args.version or v2.version()
    pool=conversations() if args.conversations else cases()
    selected=[c for c in pool if not args.only or c['id'] in args.only.split(',')]
    report=asyncio.run(run_conversations(selected,args.live,version) if args.conversations else run(selected,args.live,version))
    if args.output:args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
