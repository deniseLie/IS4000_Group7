"""Deterministic handover-note suggestions from the customer's authorised records; no model, no writes.

The customer reviews and edits every line before a separate agenda.save, and sharing needs consent.
"""
from datetime import date
from app.schemas.contracts import RequestContext
from app.schemas.customer_data import CustomerProfile
from app.schemas.relationship import RelationshipRequest
from app.schemas.relationship_intro import DraftSource,HandoverDraft,Introduction
from app.services import customer_data
from app.services.privacy import redact

LANGUAGES={'en':'English','zh':'Chinese','ms':'Malay','ta':'Tamil'}
URGENCY={'high':0,'normal':1,'low':2}
OPEN=('sent','seen')


class NoAdvisorError(LookupError):
    """No current relationship manager is recorded, so there is no recipient for a note."""


def day(value: date) -> str:
    return f'{value.day} {value:%b %Y}'


def clip(text: str,limit: int) -> str:
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'


def compose(profile: CustomerProfile,requests: list[RelationshipRequest],intro: Introduction) -> tuple[list[str],list[str]]:
    """Return suggested topics and profile fields. Wording states recorded facts only."""
    if intro.current is None:raise NoAdvisorError('No current relationship manager is recorded')
    names={intro.current.rm_id:intro.current.name}
    language=LANGUAGES.get(profile.preferred_language.lower(),profile.preferred_language)
    topics=[f'Preference: please communicate with me in {language}']
    pending=sorted((row for row in requests if row.status in OPEN),key=lambda row:(row.deadline or date.max,URGENCY[row.urgency],row.created_at))
    for row in pending[:4]:
        detail=[f'requested deadline {day(row.deadline)}'] if row.deadline else []
        detail.append(f'recorded status {row.status}')
        if row.advisor_id is not None and row.advisor_id!=intro.current.rm_id and intro.previous is not None:
            detail.append(f'raised with {intro.previous.name}')
        elif row.advisor_id in names:detail.append(f'raised with {names[row.advisor_id]}')
        if row.customer_action:detail.append(f'my recorded action: {clip(row.customer_action,120)}')
        topics.append(clip(f"Priority: {clip(redact(row.what)[0],140)} ({'; '.join(detail)})",300))
    if intro.previous is not None:
        topics.append(f'Context: previously looked after by {intro.previous.name} until {day(intro.previous.ended_on)}')
    return topics,['name','preferred_language']


async def draft(ctx: RequestContext) -> HandoverDraft:
    profile=await customer_data.get_profile_record(ctx)
    requests=await customer_data.get_request_records(ctx)
    intro=await customer_data.get_introduction_record(ctx)
    topics,fields=compose(profile.data,requests.data,intro.data)
    assert intro.data.current is not None
    return HandoverDraft(advisor_id=intro.data.current.rm_id,recipient_name=intro.data.current.name,profile_fields=fields,topics=topics,
        sources=[DraftSource(tool=tool,call_id=envelope.call_id,as_of=envelope.as_of) for tool,envelope in
            (('customer.get_profile',profile),('requests.list',requests),('rm.get_introduction',intro))])
