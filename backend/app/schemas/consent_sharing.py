"""Consent v2 reads for Data sharing (migrations 0109-0113): her cards per category, her RM's asks and who looked at what.
Checked shapes only; every write is a ledger action (consent.grant, consent.revoke, consent.decline_ask)."""
from datetime import date
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,JsonValue,StrictBool,StrictInt,StringConstraints

Code=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z][a-z0-9_.]{0,60}$')]
Label=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=600)]
Switchable=Literal['transaction_insights','chat_topics']


class Model(BaseModel):
    model_config=ConfigDict(extra='ignore')


class SharingGrant(Model):
    grant_id: UUID
    legacy: StrictBool
    scope: dict[str,JsonValue] | None
    until: Literal['date','meeting','request'] | None
    basis: Literal['consent','notice']
    granted_at: AwareDatetime
    expires_at: AwareDatetime | None
    until_meeting_id: UUID | None
    until_request_id: UUID | None
    renewal_due: StrictBool


class SharingCategory(Model):
    code: Code
    label: Label
    gated: StrictBool
    withdrawable: StrictBool
    max_months: StrictInt | None
    notice_version: str | None
    grant: SharingGrant | None


class SharingAccount(Model):
    account_id: StrictInt
    type: Label
    number: Label
    currency: Label


class SharingRm(Model):
    advisor_id: UUID
    name: Label


class NextMeeting(Model):
    meeting_id: UUID
    starts_at: AwareDatetime


class ConsentSharing(Model):
    rm: SharingRm | None
    categories: list[SharingCategory]=Field(min_length=1,max_length=12)
    accounts: list[SharingAccount]=Field(max_length=20)
    next_meeting: NextMeeting | None
    topic_areas: list[Code]=Field(max_length=12)
    today: date

    def active(self,category: str) -> SharingGrant | None:
        """Her grant of the category to her current RM, if any (the card's switch is on)."""
        return next((c.grant for c in self.categories if c.code==category and c.grant is not None),None)


class ConsentAskRecord(Model):
    ask_id: UUID
    category: Switchable
    category_label: Label
    reason: Literal['meeting_prep','review','advice']
    advisor_id: UUID
    rm_name: str | None
    meeting_id: UUID | None
    meeting_starts_at: AwareDatetime | None
    status: Literal['open','shared','declined','lapsed']
    asked_at: AwareDatetime
    lapses_at: AwareDatetime
    closed_at: AwareDatetime | None
    reference: str | None


class AccessLogRow(Model):
    """One RM read of her records (0112): ids, codes and dates only."""
    call_id: UUID | None
    at: AwareDatetime
    advisor_id: UUID | None
    relation: Literal['current_rm','former_rm','not_your_rm']
    tool: Code | None
    category: Code | None
    purpose: Literal['meeting_prep','client_view'] | None
    meeting_id: UUID | None
    outcome: Literal['read','not_shared','refused']
    basis: Literal['consent','client_shared','always_shared'] | None
    grant_id: UUID | None
