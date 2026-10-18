"""STUB Authorisation & Consent Service (architecture §5.7.4): a few fixed rules so callers can code against decide().
The real rules (agent tool allow-lists, step-up, consent registry) replace this file's bodies, not its signatures."""
from typing import Any
from uuid import UUID
from app.schemas.contracts import Decision, Principal, RequestContext

STUB = True

# Decision #2 in §13: only these action types run without customer confirmation
AUTO_TYPES = {'handoff.create', 'handoff.claim', 'handoff.close', 'appointment.hold', 'compliance.update_checklist_item', 'agenda.save', 'consent.request'}
STEP_UP_TYPES = {'card.unblock'}


def decide(subject: Principal, action: str, resource: dict[str, Any] | None = None, context: RequestContext | None = None) -> Decision:
    if subject.kind == 'guest' and not action.startswith('kb.'):
        return Decision(allow=False, reasons=['guest_no_personal_data'])
    if action in STEP_UP_TYPES and subject.aal != 'aal2':
        return Decision(allow=False, reasons=['step_up_required'])
    obligations = []
    if action == 'customer.get_credit_cards':
        obligations.append('mask:pan')
    if '.' in action and action.split('.')[0] in {'card', 'appointment', 'requests', 'rm', 'consent'} and action not in AUTO_TYPES:
        obligations.append('require:confirmation')
    return Decision(allow=True, reasons=['stub_rule'], obligations=obligations)


def has_consent(customer_id: UUID, purpose: str, recipient: str) -> dict:
    return {'granted': False, 'grant_id': None}
