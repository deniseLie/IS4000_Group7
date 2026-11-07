"""Render the canonical saved meeting brief in the RM assistant without a second writer or approval path."""
from pydantic import JsonValue,TypeAdapter
from app.schemas.meeting_preparation import PreparedBrief


# Where each cited reference comes from, in the words the RM uses (storyline: Chat, Account, Portfolio, Compliance case).
TITLES={'rm_client_profile':'Client record (profile, accounts, portfolio, dates)','rm_review_status':'Compliance case',
    'rm_tickets':'Requests','rm_meeting_notes':'Meeting notes','rm_transaction_insights':'Shared transaction insights',
    'rm_talking_points':'Chat (topics she shared)','meeting_followups':'Follow-ups and tasks','rm_collateral_alert':'Collateral alert'}


def title(reference: str) -> str:
    reader=reference.split(':',1)[0]
    return TITLES.get(reader,reader.replace('_',' '))


def reply(prepared: PreparedBrief,context: dict[str,JsonValue]) -> dict[str,JsonValue]:
    labels={'record':'record','client_said':'client_stated','ai_suggestion':'suggestion','gap':'gap'}
    sections=[{'heading':section.heading,'items':[{'text':item.text,'kind':labels[item.label],'owner':'none',
        'source_ids':item.sources} for item in section.items]} for section in prepared.card.sections]
    references=list(dict.fromkeys(reference for section in prepared.card.sections for item in section.items for reference in item.sources))
    sources=[{'id':reference,'title':title(reference),'kind':'record',
        'as_of':prepared.card.as_of.isoformat(),'record':{'reference':reference}} for reference in references]
    state='Sources changed; refresh and review the brief in the meeting workspace.' if prepared.sources_changed else 'Review decisions are made in the meeting workspace.'
    return TypeAdapter(dict[str,JsonValue]).validate_python({'status':'ok','intent':'meeting_brief',
        'summary':f'Saved meeting brief, version {prepared.card.version}. '+state,'sections':sections,'clarification':None,
        'context':context,'sources':sources,'origin':'ai',
        'validation':{'checked':sum(len(section.items) for section in prepared.card.sections),'withheld':prepared.card.withheld,'reasons':[]},
        'meeting_brief':{'meeting_id':str(prepared.card.meeting_id),'brief_id':str(prepared.brief_id),'version':prepared.card.version,
            'content_hash':prepared.content_hash,'decision':prepared.decision,'sources_changed':prepared.sources_changed},
        'meeting_workspace':str(prepared.card.meeting_id)})
