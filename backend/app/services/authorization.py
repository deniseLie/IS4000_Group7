"""Explicit tool policy. decide() is pure; require_permission() audits and enforces it.

Agent identity is attached by trusted backend code in RequestContext, never by model arguments.
Consent lookup and transactional action execution belong to their subsequent service stages.
"""
from collections.abc import Mapping
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, ValidationError
from app.schemas.contracts import Decision, Principal, RequestContext
from app.services import audit
from app.services.agents.registry import TOOLS

PUBLIC_TOOLS = frozenset({'kb.search','kb.get_fact','tickets.categories'})
AUTO_TYPES = frozenset({'handoff.create','appointment.hold','compliance.update_checklist_item','agenda.save','consent.request','document.register','document.submit','compliance.answer_request'})
# The customer's own card changes: confirmed by the signed-in customer only; agents are refused (customer_only_action).
# Staff run the same two types through scripts/staff_support.py, which never goes through this policy.
CUSTOMER_CARD_CHANGES = frozenset({'card.unblock','card.cancel_permanently'})
CONFIRM_TYPES = frozenset({'card.temporary_block','card.dispute','card.order_replacement','appointment.confirm','appointment.cancel',
    'requests.create','rm.share_summary','consent.grant','consent.revoke','ticket.create'}) | CUSTOMER_CARD_CHANGES
STAFF_TYPES = frozenset({'card.unblock','card.cancel_permanently','handoff.claim','handoff.close'})
BANK_ONLY_CARD_ACTIONS = frozenset({'card.temporary_block','card.dispute','card.unblock','card.cancel_permanently'})
AGENT_ACTIONS = {
    'fraud':frozenset({'card.order_replacement'}),
    'relationship':frozenset({'appointment.hold','appointment.confirm','appointment.cancel','requests.create',
        'rm.share_summary','consent.grant','consent.revoke'}),
}
KNOWN_TOOLS = frozenset(tool for tools in TOOLS.values() for tool in tools) | AUTO_TYPES | CONFIRM_TYPES | STAFF_TYPES | {'compliance.workspace','documents.precheck','actions.read','actions.list','actions.create_case','consent.records','agenda.list','documents.list','documents.prepare_upload','documents.download','documents.inspect','notifications.list','notifications.read','appointments.meeting_options','tickets.list','tickets.categories','tickets.follow_up','tickets.attach'}


class PolicyResource(BaseModel):
    """Only a trusted customer scope affects policy; unrelated action fields are ignored."""
    model_config = ConfigDict(extra='ignore')
    customer_id: StrictInt | None = Field(default=None,gt=0)


class PolicyDenied(PermissionError):
    """A registered tool or action was refused before any service access."""


def decide(subject: Principal, action: str, resource: Mapping[str, JsonValue] | None, context: RequestContext | None) -> Decision:
    if action not in KNOWN_TOOLS:return Decision(allow=False,reasons=['unknown_tool'])
    if context is None:return Decision(allow=False,reasons=['missing_context'])
    if subject != context.principal:return Decision(allow=False,reasons=['principal_mismatch'])
    try:
        scope = PolicyResource.model_validate(resource if resource is not None else {})
    except ValidationError:
        return Decision(allow=False,reasons=['invalid_resource_scope'])
    if subject.kind == 'system':return Decision(allow=False,reasons=['system_access_unconfigured'])
    if subject.kind == 'guest':
        if action not in PUBLIC_TOOLS:return Decision(allow=False,reasons=['guest_no_personal_data'])
        if scope.customer_id is not None:return Decision(allow=False,reasons=['customer_scope_mismatch'])
    else:
        if not subject.user_id or not subject.jwt or not subject.jwt.get_secret_value().strip():
            return Decision(allow=False,reasons=['customer_session_required'])
        if action not in PUBLIC_TOOLS and (subject.customer_id is None or subject.customer_id <= 0):
            return Decision(allow=False,reasons=['customer_session_required'])
        if scope.customer_id is not None and scope.customer_id != subject.customer_id:
            return Decision(allow=False,reasons=['customer_scope_mismatch'])
    # Card actions are the bank's (0081): clients and agents cannot block, dispute, unblock or cancel; staff use scripts/staff_support.py.
    if action in BANK_ONLY_CARD_ACTIONS:return Decision(allow=False,reasons=['bank_only_action'])
    if action in STAFF_TYPES and action not in CUSTOMER_CARD_CHANGES:return Decision(allow=False,reasons=['staff_access_required'])
    if context.agent is not None:
        allowed = action in TOOLS[context.agent] or action in AGENT_ACTIONS.get(context.agent,frozenset())
        if not allowed:return Decision(allow=False,reasons=['agent_tool_forbidden'])
    obligations: list[str] = []
    if action == 'customer.get_credit_cards':obligations.append('mask:pan')
    if action in CONFIRM_TYPES:obligations.append('require:confirmation')
    if action == 'rm.share_summary':obligations.append('require:consent:rm_brief')
    return Decision(allow=True,reasons=['explicit_rule'],obligations=obligations)


def require_permission(subject: Principal, action: str, resource: Mapping[str, JsonValue] | None, context: RequestContext) -> Decision:
    """Record ids and rule codes only; a denial raises before the caller opens a DB client."""
    decision = decide(subject,action,resource,context)
    actor = {'actor_type':'agent','actor_id':context.agent} if context.agent is not None else {}
    audit.add('policy','policy.decided','success' if decision.allow else 'denied',
        details={'tool':action if action in KNOWN_TOOLS else 'unregistered_tool','reasons':decision.reasons,'version':'v1'},**actor)
    if not decision.allow:raise PolicyDenied(f"Tool access denied: {decision.reasons[0]}")
    return decision
