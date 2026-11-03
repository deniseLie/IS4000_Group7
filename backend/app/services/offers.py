"""v5 offers (build plan phase 4; migration 0100): what agents propose, saved as proposed commands on the Action Service.

One reply's offers form one group, confirmed or cancelled as a whole in group order. Nothing an agent proposes runs
until the client confirms, by button or by a plain English yes when exactly one group is open. SQL checks ownership,
the TTL and the order again; a double confirm records once."""
import re
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from app.core import data_api
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand, Offer, RequestContext, TargetAgent
from app.services.actions import canonical_target
from app.services.actions.store import ActionIntegrityError, checked
from app.services.authorization import PolicyDenied, require_permission

TTL_MINUTES = 30
MAX_GROUP = 10  # SQL bounds group_order to 1..10


class Proposal(BaseModel):
    """One action an agent offers; the target comes from code-read records, never from the model."""
    model_config = ConfigDict(extra='forbid')
    task_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    agent: TargetAgent
    type: str
    target: dict[str, JsonValue] = {}
    summary: str = Field(min_length=1, max_length=300)


class OpenOffer(BaseModel):
    model_config = ConfigDict(extra='ignore')
    command_id: UUID
    offer_group: UUID
    group_order: int
    type: str
    target: dict[str, JsonValue]
    case_id: UUID
    turn_id: UUID | None = None
    expires_at: datetime


def group_for(case_id: UUID) -> UUID:
    """One group per case; a case is one turn, so a replayed turn saves the same group."""
    return uuid5(NAMESPACE_URL, f'demobank-offer-group:{case_id}')


async def save(proposals: list[Proposal], ctx: RequestContext, turn_id: UUID | None = None) -> list[Offer]:
    """Save a reply's offers in order. Each needs its agent's spec to list the type (authorization.decide)."""
    if not proposals:return []
    if ctx.case_id is None or ctx.principal.kind != 'customer':raise ActionIntegrityError('Offers need a signed-in customer case')
    if len(proposals) > MAX_GROUP:raise ActionIntegrityError(f'At most {MAX_GROUP} offers in one reply')
    for proposal in proposals:  # every refusal before the database is opened
        require_permission(ctx.principal, proposal.type, {'customer_id': ctx.principal.customer_id}, ctx.model_copy(update={'agent': proposal.agent}))
    group = group_for(ctx.case_id)
    saved: list[Offer] = []
    with data_api.connect(ctx) as db:
        for order, proposal in enumerate(proposals, 1):
            key = f'{ctx.case_id}:{proposal.task_id}:{proposal.type}'
            command = ActionCommand(command_id=uuid5(NAMESPACE_URL, 'demobank-offer:' + key), idempotency_key=key, type=proposal.type, target=proposal.target)
            target = canonical_target(command)
            response = await data_api.execute(lambda: db.rpc('action_offer', {'p_command_id': str(command.command_id), 'p_case_id': str(ctx.case_id),
                'p_type': command.type, 'p_target': target, 'p_idempotency_key': key, 'p_ttl_minutes': TTL_MINUTES,
                'p_offer_group': str(group), 'p_group_order': order, 'p_turn_id': str(turn_id) if turn_id else None}).execute(), 'offers.save')
            result = checked(response.data, ctx)
            if result.command.idempotency_key != key or result.command.case_id != ctx.case_id:
                raise ActionIntegrityError('The saved offer does not match the proposal')
            saved.append(Offer(offer_id=result.command.command_id, type=proposal.type, target=target, summary=proposal.summary,
                expires_at=result.command.expires_at))
    return saved


async def open_groups(ctx: RequestContext, conversation_id: UUID) -> dict[UUID, list[OpenOffer]]:
    """The client's unexpired offers in one conversation, by group (newest first), each in group order."""
    with data_api.connect(ctx) as db:
        response = await data_api.execute(lambda: db.rpc('customer_open_offers', {'p_conversation_id': str(conversation_id)}).execute(), 'offers.open')
    groups: dict[UUID, list[OpenOffer]] = {}
    for item in response.data or []:
        offer = OpenOffer.model_validate(item)
        groups.setdefault(offer.offer_group, []).append(offer)
    return {group: sorted(items, key=lambda o: o.group_order) for group, items in groups.items()}


async def open_group(ctx: RequestContext, conversation_id: UUID) -> tuple[UUID, list[OpenOffer]] | None:
    """The one open group a typed yes or no may answer; None when there is none or more than one."""
    groups = await open_groups(ctx, conversation_id)
    return next(iter(groups.items())) if len(groups) == 1 else None


YES = {'yes', 'y', 'yeah', 'yep', 'yup', 'sure', 'ok', 'okay', 'confirm', 'confirmed', 'go ahead', 'do it', 'please do',
    'yes please', 'yes do it', 'yes go ahead', 'yes confirm', 'yes that s right', 'yes thanks', 'yes thank you', 'ok go ahead'}
NO = {'no', 'n', 'nope', 'cancel', 'no thanks', 'no thank you', 'don t', 'do not', 'stop', 'not now', 'no cancel', 'no don t'}


def reply_kind(text: str) -> str:
    """'yes', 'no' or 'other'. English words only, and only a whole short reply: "yes but also …" is 'other'."""
    words = re.sub(r"[^a-z]+", ' ', text.lower().replace('’', "'")).strip()
    return 'yes' if words in YES else 'no' if words in NO else 'other'


def customer_only(ctx: RequestContext) -> None:
    if ctx.agent is not None or ctx.principal.kind != 'customer':raise PolicyDenied('Only the signed-in customer can confirm or cancel an offer')


async def confirm(group_id: UUID, ctx: RequestContext) -> list[ActionOutcome]:
    """Run the group in order; each receipt can fill the next target; a step that does not succeed stops the rest."""
    customer_only(ctx)
    with data_api.connect(ctx) as db:
        response = await data_api.execute(lambda: db.rpc('offer_group_confirm', {'p_offer_group': str(group_id)}).execute(), 'offers.confirm')
    return [checked(item, ctx) for item in response.data or []]


async def cancel(group_id: UUID, ctx: RequestContext) -> list[ActionOutcome]:
    customer_only(ctx)
    with data_api.connect(ctx) as db:
        response = await data_api.execute(lambda: db.rpc('offer_group_cancel', {'p_offer_group': str(group_id)}).execute(), 'offers.cancel')
    return [checked(item, ctx) for item in response.data or []]
