"""Render the canonical saved meeting brief in the RM assistant without a second writer or approval path."""
from pydantic import JsonValue,TypeAdapter
from app.schemas.meeting_preparation import PreparedBrief


def reply(prepared: PreparedBrief,context: dict[str,JsonValue]) -> dict[str,JsonValue]:
    labels={'record':'record','client_said':'client_stated','ai_suggestion':'suggestion','gap':'gap'}
    sections=[{'heading':section.heading,'items':[{'text':item.text,'kind':labels[item.label],'owner':'none',
        'source_ids':item.sources} for item in section.items]} for section in prepared.card.sections]
    references=list(dict.fromkeys(reference for section in prepared.card.sections for item in section.items for reference in item.sources))
    sources=[{'id':reference,'title':reference.split(':',1)[0].replace('_',' '),'kind':'record',
        'as_of':prepared.card.as_of.isoformat(),'record':{'reference':reference}} for reference in references]
    state='Sources changed; refresh and review the brief in the meeting workspace.' if prepared.sources_changed else 'Review decisions are made in the meeting workspace.'
    return TypeAdapter(dict[str,JsonValue]).validate_python({'status':'ok','intent':'meeting_brief',
        'summary':f'Saved meeting brief, version {prepared.card.version}. '+state,'sections':sections,'clarification':None,
        'context':context,'sources':sources,'origin':'ai',
        'validation':{'checked':sum(len(section.items) for section in prepared.card.sections),'withheld':prepared.card.withheld,'reasons':[]},
        'meeting_brief':{'meeting_id':str(prepared.card.meeting_id),'brief_id':str(prepared.brief_id),'version':prepared.card.version,
            'content_hash':prepared.content_hash,'decision':prepared.decision,'sources_changed':prepared.sources_changed},
        'meeting_workspace':str(prepared.card.meeting_id)})
