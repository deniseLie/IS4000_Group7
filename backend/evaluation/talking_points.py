"""Talking points evaluation (0108, build plan phase 4): precision and recall per topic for lane 1, on hand-labelled messages.

Offline (default): python -m evaluation.talking_points   (code only: care, the pre-filter and lane 2's rule; no model calls)
Live (opt-in):     python -m evaluation.talking_points --live --output talking-points-results.json

Each case runs the steps `talking_points.after_reply` runs, without a database: the guardrail on her (redacted) history,
the care pause, the code pre-filter, then (live only) the real `talking_points/v1` call through `llm_gateway.call`
(TALKING_POINTS_MODEL, else LLM_MODEL; no retry) and the code validators. A case's prediction is the set of topics a
point would be recorded for. Offline, a case the pre-filter passes has no prediction, so only the pre-filter is scored
(positives it lets through, negatives it stops before a model call). Lane 2 (`share_candidate`, a rule) is scored in both
modes: a Let-your-RM-know offer on a case labelled with no topic is a false prompt.

Cases in talking_points_cases.json are synthetic and labelled by hand: `expect` maps each topic that should be recorded
to its stage (empty: nothing should be stored). Groups: positive, multi, previous (her earlier message as context),
negative, sensitive, care and injection. A live run is one sample of a non-deterministic model: review the misses."""
import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from app.core.config import settings
from app.core.observability import start_trace
from app.schemas.chat import ChatRequest
from app.services import llm_gateway,prompts,talking_points as tp
from app.services.guardrail import assess_request

HERE=Path(__file__).parent
CASES=HERE/'talking_points_cases.json'


def cases(path: Path=CASES) -> list[dict]:
    return json.loads(path.read_text(encoding='utf-8'))


def assessed(case: dict):
    history=[*([{'role':'user','content':case['previous']},{'role':'assistant','content':'Here is what I found.'}] if case.get('previous') else []),
        {'role':'user','content':case['message']}]
    return assess_request(ChatRequest(messages=history,mode='personal'))


async def classify(case: dict,live: bool) -> dict:
    """What lane 1 would record for this case: {'gate', 'topics' (None offline when the model would be asked), 'stages', 'dropped'}."""
    risk=assessed(case)
    if risk.care:return {'gate':'care','topics':[],'stages':{},'dropped':{}}
    reason=tp.prefilter(risk.redacted_message,risk)
    if reason:return {'gate':reason,'topics':[],'stages':{},'dropped':{}}
    if not live:return {'gate':'model','topics':None,'stages':{},'dropped':{}}
    prompt=prompts.load('talking_points',tp.PROMPT)
    start_trace()
    try:
        result=await llm_gateway.call(prompt,tp.system_text(prompt),tp.model_input(risk.redacted_message,case.get('previous')),tp.SCHEMA,
            model=settings.talking_points_model or None,retries=0)
    except llm_gateway.LLMError as error:
        return {'gate':'model','topics':[],'stages':{},'dropped':{},'error':error.reason}
    kept,dropped=tp.validate(result.data.get('points') or [],risk.redacted_message,set())
    return {'gate':'model','topics':[p['topic'] for p in kept],'stages':{p['topic']:p['stage'] for p in kept},'dropped':dict(dropped),
        'quotes':[p['quote'] for p in kept],'proposed':len(result.data.get('points') or [])}


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
        'stage_agreement':ratio(stages['same'],stages['same']+stages['different'])}


async def run(selected: list[dict],live: bool) -> dict:
    rows=[]
    for case in selected:
        row={'id':case['id'],'group':case['group'],'expect':case['expect'],**await classify(case,live),'share':lane_two(case)}
        rows.append(row)
    gates=Counter(r['gate'] for r in rows)
    labelled=[r for r in rows if r['expect']]
    report={'mode':'live' if live else 'offline','cases':len(rows),'dataset_sha256':hashlib.sha256(CASES.read_bytes()).hexdigest(),
        'prompt_version':prompts.load('talking_points',tp.PROMPT)['id'],'model':llm_gateway.model_name(settings.talking_points_model or None) if live else None,
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


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live',action='store_true',help='Call the real model for each case the pre-filter passes')
    parser.add_argument('--only',help='Comma list of case ids')
    parser.add_argument('--output',type=Path,help='Write the full report as JSON')
    args=parser.parse_args()
    selected=[c for c in cases() if not args.only or c['id'] in args.only.split(',')]
    report=asyncio.run(run(selected,args.live))
    if args.output:args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
