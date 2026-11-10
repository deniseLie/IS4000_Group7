"""Synthetic meeting quality checks and independent Codex judging, with opt-in redacted LangSmith publication.

The native integration test drives the real API/SQL writer and writes results. Publish only that reviewed synthetic
result file: python -m evaluation.meetings --publish /absolute/path/meeting-eval-results.json --project demobank-dev.
No hosted records are loaded. Existing datasets/examples are updated by stable case IDs; unrelated examples remain.
"""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL,uuid4,uuid5
from pydantic import BaseModel,ConfigDict,Field,JsonValue
from app.schemas.meeting_lifecycle import Cue,MinutesContent,Text
from app.services.privacy import redact

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,hide_input_in_errors=True)

class ExpectedAction(Model):
    owner: Literal['rm','client','unassigned']
    commitment: Literal['agreed','conditional','tentative','unclear']
    terms: tuple[str,...]
    required: bool

class Case(Model):
    id: str
    title: Text
    rubric: Text
    cues: tuple[tuple[Literal['rm','client','unmapped'],Text],...]
    actions: tuple[ExpectedAction,...]
    forbidden: tuple[str,...]
    maximum_actions: int

class Check(Model):
    key: str
    score: float=Field(ge=0,le=1)
    comment: str

class Judgement(Model):
    faithfulness: Literal[0,0.5,1]
    privacy: Literal[0,0.5,1]
    actions: Literal[0,0.5,1]
    usefulness: Literal[0,0.5,1]
    reason: Text=Field(max_length=1000)

class EvaluationRow(Model):
    case: Case
    content: MinutesContent|None
    error: str|None
    checks: tuple[Check,...]
    judge: Judgement|None
    producer: str
    prompt_id: str
    judge_model: str|None
    overview_prompt_id: str|None=None
    source: Literal['native_meeting_api','hosted_synthetic_meeting_api']='native_meeting_api'


def cases() -> tuple[Case,...]:
    return tuple(Case.model_validate(item) for item in json.loads(Path(__file__).with_name('meeting_cases.json').read_text()))


def cues_for(case: Case) -> tuple[Cue,...]:
    return tuple(Cue(cue_id='cue'+str(index),speaker=speaker,start_seconds=None,text=text) for index,(speaker,text) in enumerate(case.cues,1))


def publication_inputs(row: EvaluationRow) -> dict[str,JsonValue]:
    return {'case':row.case.model_dump(mode='json'),'turns':[{'say':speaker+': '+text} for speaker,text in row.case.cues]}


def publication_outputs(row: EvaluationRow) -> dict[str,JsonValue]:
    content=row.content
    lines=[] if content is None else [content.title,*[item.text for item in content.summary],
        *['Decision: '+item.text for item in content.decisions],
        *['Draft follow-up ('+item.owner+', '+item.commitment+'): '+item.text+(' Condition: '+item.condition if item.condition else '') for item in content.actions]]
    return {'answer':'\n'.join(lines),'minutes':content.model_dump(mode='json') if content else None,
        'checks':[item.model_dump() for item in row.checks],'judge':row.judge.model_dump() if row.judge else None,
        'agents':{'minutes_writer':1},'prompt_ids':['minutes_writer/'+row.prompt_id,*(['minutes_overview/'+row.overview_prompt_id] if row.overview_prompt_id else [])]}


def publication_metadata(rows: tuple[EvaluationRow,...]) -> dict[str,JsonValue]:
    return {'model':', '.join(sorted({row.producer for row in rows})),'transport':'codex','pipeline':'meeting',
        'judge_model':', '.join(sorted({row.judge_model for row in rows if row.judge_model})),
        'answer_prompt':', '.join(sorted({prompt for row in rows for prompt in ['minutes_writer/'+row.prompt_id,*(['minutes_overview/'+row.overview_prompt_id] if row.overview_prompt_id else [])]})),
        'timing_kind':'result_publication','synthetic':True}


def checks(case: Case,content: MinutesContent) -> tuple[Check,...]:
    cues={cue.cue_id:cue for cue in cues_for(case)}
    claims=(*content.summary,*content.decisions,*content.actions)
    quotes=all(item.cue_id in cues and item.quote in cues[item.cue_id].text for item in claims)
    owners=all(item.cue_id in cues and item.owner=={'rm':'rm','client':'client','unmapped':'unassigned'}[cues[item.cue_id].speaker] for item in content.actions)
    from app.services.meetings.writer import quoted_dates
    dates=all(item.cue_id in cues and (item.due_on is None or item.due_quote is not None
        and item.due_on in quoted_dates(item.due_quote) and item.due_quote in cues[item.cue_id].text) for item in content.actions)
    conditions=all(item.cue_id in cues and (item.commitment!='conditional' or item.condition is not None and item.condition in cues[item.cue_id].text) for item in content.actions)
    matched=[any(item.owner==expected.owner and item.commitment==expected.commitment and all(term.lower() in item.text.lower() for term in expected.terms) for item in content.actions) for expected in case.actions if expected.required]
    text=' '.join([*(item.text for item in content.summary),*(item.text for item in content.decisions),*(item.text for item in content.actions),content.client_recap]).lower()
    forbidden=[phrase for phrase in case.forbidden if phrase.lower() in text]
    unexpected_agreed=any(item.commitment=='agreed' and not any(item.owner==expected.owner and expected.commitment=='agreed' and all(term.lower() in item.text.lower() for term in expected.terms) for expected in case.actions) for item in content.actions)
    results=[('source_quotes',quotes,'Every quote exists in its named cue'),('speaker_ownership',owners,'Every owner follows the verified speaker metadata'),
        ('explicit_dates',dates,'Dates require an explicit calendar date and exact quote'),('conditional_evidence',conditions,'Conditions retain exact source evidence'),
        ('expected_action_recall',all(matched),'All expected owner/commitment actions are present'),
        ('action_precision',not unexpected_agreed and len(content.actions)<=case.maximum_actions,'No unsupported agreed task or excess action'),
        ('forbidden_claims',not forbidden,'Forbidden claim checks: '+(', '.join(forbidden) or 'none'))]
    return tuple(Check(key=key,score=int(passed),comment=comment) for key,passed,comment in results)


async def judge(case: Case,content: MinutesContent) -> Judgement:
    from app.services import codex_cli
    from evaluation.evaluators import judge_name
    name=judge_name()
    system=Path(__file__).parent.joinpath('judge','meeting-v2.md').read_text()
    payload=redact(json.dumps({'expected':case.rubric,'cues':[cue.model_dump(mode='json') for cue in cues_for(case)],'minutes':content.model_dump(mode='json')},ensure_ascii=False))[0]
    value,usage=await codex_cli.answer_with_model(system,Judgement.model_json_schema(),[('user',payload)],name)
    return Judgement.model_validate(value)


def publish(path: Path,project: str) -> dict[str,str|int]:
    from app.core.langsmith_client import client,scrub
    rows=tuple(EvaluationRow.model_validate(item) for item in json.loads(path.read_text()))
    if not rows:raise ValueError('No synthetic meeting evaluation rows to publish')
    connection=client();connection.read_project(project_name=project)
    dataset_name='demobank-meetings'
    dataset=connection.read_dataset(dataset_name=dataset_name) if connection.has_dataset(dataset_name=dataset_name) else connection.create_dataset(dataset_name,description='Synthetic meeting Minutes Writer cases: evidence, ownership, conditions, instructions and explicit RM approval boundaries.')
    label='meeting-eval-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    experiment=connection.create_project(label,reference_dataset_id=dataset.id,metadata=publication_metadata(rows),description='Published isolated native and approved synthetic hosted meeting API/SQL results with real Codex writer and independent judge. Run latency is publication time, not model latency.')
    existing={example.id for example in connection.list_examples(dataset_id=dataset.id)}
    for row in rows:
        example_id=uuid5(NAMESPACE_URL,'demobank-meetings/'+row.case.id)
        inputs=scrub(publication_inputs(row));outputs=scrub({'check':row.case.rubric})
        if example_id in existing:connection.update_example(example_id,inputs=inputs,outputs=outputs,metadata={'case_id':row.case.id,'synthetic':True})
        else:connection.create_example(inputs=inputs,outputs=outputs,dataset_id=dataset.id,example_id=example_id,metadata={'case_id':row.case.id,'synthetic':True})
        run_id=uuid4();now=datetime.now(timezone.utc)
        connection.create_run(row.case.id,inputs,'chain',id=run_id,project_name=label,reference_example_id=example_id,
            outputs=publication_outputs(row),
            error=row.error,start_time=now,end_time=now,extra={'metadata':{'synthetic':True,'producer':row.producer,'prompt_version':row.prompt_id,'overview_prompt_version':row.overview_prompt_id,'judge_model':row.judge_model,'source':row.source}},tags=['eval','meeting','isolated','synthetic'])
        for item in row.checks:connection.create_feedback(run_id,key=item.key,score=item.score,comment=item.comment,session_id=experiment.id)
        if row.judge:
            connection.create_feedback(run_id,key='judge_rubric',score=min(row.judge.faithfulness,row.judge.privacy,row.judge.actions,row.judge.usefulness),comment=row.judge.reason,session_id=experiment.id)
            for key in ('faithfulness','privacy','actions','usefulness'):
                connection.create_feedback(run_id,key='judge_'+key,score=getattr(row.judge,key),comment=row.judge.reason,session_id=experiment.id)
    connection.flush()
    return {'dataset':dataset_name,'experiment':label,'experiment_id':str(experiment.id),'url':experiment.url,'cases':len(rows)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--publish',type=Path,required=True);parser.add_argument('--project',required=True)
    args=parser.parse_args();print(json.dumps(publish(args.publish,args.project)))
