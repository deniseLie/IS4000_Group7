"""Independent synthetic preparation grading. Brief and pack keep separate typed contexts and deterministic checks.

Run tests with TEST_MEETING_PREP_JUDGE=1 and TEST_MEETING_PREP_EVAL_OUTPUT=/private/results.json. Publish only reviewed
synthetic results: python -m evaluation.meeting_preparation --publish /private/results.json --project demobank-dev.
"""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL,uuid4,uuid5
from pydantic import JsonValue,model_validator
from app.schemas.meeting_preparation import HiddenInput,PreparationInputs,PreparedBrief
from app.schemas.meeting_packs import PackInputs,PackView
from app.services.meetings.preparation import QUESTIONS,live_sources
from app.services.meetings.packs import safe_context
from app.services.privacy import redact
from app.services.prompts import load
from evaluation.meetings import Check,Judgement

class BriefInput(HiddenInput):
    frame: PreparationInputs
    output: PreparedBrief

class PackInput(HiddenInput):
    frame: PackInputs
    output: PackView

class PreparationResult(HiddenInput):
    case_id: str
    agent: Literal['brief_writer','proposal_drafter']
    rubric: str
    evidence: BriefInput|PackInput
    checks: tuple[Check,...]
    judge: Judgement|None
    error: Literal['judge_not_completed']|None
    producer: str
    prompt_id: str
    judge_model: str

    @model_validator(mode='after')
    def completed_judge(self) -> 'PreparationResult':
        if (self.judge is None)!=(self.error is not None):raise ValueError('A preparation result needs either a completed judge or an explicit unfinished-judge error')
        return self


def brief_checks(frame: PreparationInputs,prepared: PreparedBrief) -> tuple[Check,...]:
    sources=live_sources(frame)
    readers={source.reference.split(':',1)[0] for source in sources}
    items=[item for section in prepared.card.sections for item in section.items if item.label!='gap']
    # Each separate SQL snapshot has fresh audited call IDs; compare its reader and exact record text.
    cited=all(item.sources and all(reference.split(':',1)[0] in readers for reference in item.sources) for item in items)
    grounded=all(item.label=='ai_suggestion' and item.text in QUESTIONS.values() or any(source.reference.split(':',1)[0]==reference.split(':',1)[0] and source.text in item.text
        for reference in item.sources for source in sources) for item in items)
    return (Check(key='brief_citations',score=int(cited),comment='Every non-gap item cites an authorised live reader'),
        Check(key='brief_grounding',score=int(grounded),comment='Each item is rendered from its cited current record'),
        Check(key='brief_current_sources',score=int(not prepared.sources_changed),comment='No stale source snapshot was approved'))


def pack_checks(frame: PackInputs,pack: PackView) -> tuple[Check,...]:
    catalogue={item.item_id:item for item in frame.catalogue}
    terms=all(item.item_id in catalogue and item==catalogue[item.item_id] for item in pack.content.products)
    context=safe_context(frame).model_dump(mode='json')
    return (Check(key='pack_catalogue_figures',score=int(terms),comment='Product terms equal the selected approved catalogue rows'),
        Check(key='pack_explicit_amounts',score=int(pack.content.stated_amounts==frame.amounts),comment='Only explicitly entered amounts appear'),
        Check(key='pack_agent_input_boundary',score=int(set(context)=={'purpose','topics','catalogue','amount_currencies'}),comment='The Drafter cannot access the internal brief or banking readers'),
        Check(key='pack_not_automatically_shared',score=int(pack.status=='draft'),comment='The model leaves the document private for separate RM approval and sharing'))


async def judged_brief(frame: PreparationInputs,prepared: PreparedBrief) -> Judgement:
    payload={'audience':'assigned RM only','purpose':frame.purpose,'topics':frame.topics,
        'authorised_sources':[item.model_dump(mode='json',exclude={'grant_id'}) for item in live_sources(frame)],'gaps':[item.model_dump() for item in frame.gaps],
        'brief':prepared.card.model_dump(mode='json')}
    return await grade(payload)


async def judged_pack(frame: PackInputs,pack: PackView) -> Judgement:
    payload={'audience':'client-facing discussion document, still a private draft','purpose':frame.purpose,'topics':frame.topics,
        'approved_catalogue':[item.model_dump(mode='json') for item in frame.catalogue],
        'explicit_amounts':[item.model_dump(mode='json') for item in frame.amounts],'pack':pack.content.model_dump(mode='json')}
    return await grade(payload)


async def grade(payload: dict[str,JsonValue]) -> Judgement:
    from app.services import codex_cli
    from evaluation.evaluators import judge_name
    prompt=Path(__file__).parent.joinpath('judge','meeting-preparation-v1.md').read_text()
    answer,usage=await codex_cli.answer_with_model(prompt,Judgement.model_json_schema(),[('user',redact(json.dumps(payload,ensure_ascii=False))[0])],judge_name())
    return Judgement.model_validate(answer)


def publish(path: Path,project: str) -> dict[str,str|int]:
    from app.core.langsmith_client import client,scrub
    rows=tuple(PreparationResult.model_validate(row) for row in json.loads(path.read_text()))
    if not rows:raise ValueError('No synthetic preparation results to publish')
    unfinished=[row.case_id for row in rows if row.error is not None]
    if unfinished:raise ValueError('Cannot publish unfinished preparation judges: '+', '.join(unfinished))
    connection=client();connection.read_project(project_name=project);name='demobank-meeting-preparation'
    dataset=connection.read_dataset(dataset_name=name) if connection.has_dataset(dataset_name=name) else connection.create_dataset(name,description='Synthetic Brief Writer and Proposal Drafter: audience boundaries, current cited facts, exact catalogue figures and human approvals.')
    label='preparation-eval-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    experiment=connection.create_project(label,reference_dataset_id=dataset.id,metadata={'pipeline':'meeting','transport':'codex','synthetic':True,'timing_kind':'result_publication',
        'model':', '.join(sorted({row.producer for row in rows})),'judge_model':', '.join(sorted({row.judge_model for row in rows})),
        'answer_prompt':', '.join(sorted({row.agent+'/'+row.prompt_id for row in rows}))},description='Published real native preparation-agent results. Publication timing is not model latency.')
    existing={item.id for item in connection.list_examples(dataset_id=dataset.id)}
    for row in rows:
        example=uuid5(NAMESPACE_URL,name+'/'+row.case_id)
        inputs=scrub({'say':row.rubric,'evidence':row.evidence.model_dump(mode='json')});expected={'check':row.rubric}
        if example in existing:connection.update_example(example,inputs=inputs,outputs=expected,metadata={'case_id':row.case_id,'synthetic':True})
        else:connection.create_example(inputs=inputs,outputs=expected,dataset_id=dataset.id,example_id=example,metadata={'case_id':row.case_id,'synthetic':True})
        run_id=uuid4();now=datetime.now(timezone.utc)
        output=row.evidence.output.card.model_dump_json() if isinstance(row.evidence,BriefInput) else row.evidence.output.content.model_dump_json()
        connection.create_run(row.case_id,inputs,'chain',id=run_id,project_name=label,reference_example_id=example,start_time=now,end_time=now,
            outputs={'answer':output,'checks':[item.model_dump() for item in row.checks],'judge':row.judge.model_dump(),'agents':{row.agent:1},'prompt_ids':[row.agent+'/'+row.prompt_id]},
            extra={'metadata':{'model':row.producer,'judge_model':row.judge_model,'prompt_version':row.prompt_id,'synthetic':True}},tags=['eval','meeting','preparation','synthetic'])
        for item in row.checks:connection.create_feedback(run_id,key=item.key,score=item.score,comment=item.comment,session_id=experiment.id)
        connection.create_feedback(run_id,key='judge_rubric',score=min(row.judge.faithfulness,row.judge.privacy,row.judge.actions,row.judge.usefulness),comment=row.judge.reason,session_id=experiment.id)
        for key in ('faithfulness','privacy','actions','usefulness'):
            connection.create_feedback(run_id,key='judge_'+key,score=getattr(row.judge,key),comment=row.judge.reason,session_id=experiment.id)
    connection.flush()
    return {'dataset':name,'experiment':label,'experiment_id':str(experiment.id),'url':experiment.url,'cases':len(rows)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--publish',type=Path,required=True);parser.add_argument('--project',required=True)
    args=parser.parse_args();print(json.dumps(publish(args.publish,args.project)))
