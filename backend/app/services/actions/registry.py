"""Executor contracts. SQL's private actions.executors registry owns transactional dispatch."""
from collections.abc import Callable
from app.schemas.actions import CustomerActionType,InputTarget
from app.schemas.contracts import ActionCommand
from app.services.actions import appointments,cards,handoffs,records

EXECUTORS: dict[CustomerActionType,Callable[[ActionCommand],InputTarget]] = {
    'card.temporary_block': cards.temporary_block,
    'card.dispute': cards.dispute,
    'card.unblock': cards.change,
    'card.cancel_permanently': cards.change,
    'handoff.create': handoffs.create,
    'appointment.hold': appointments.hold,
    'appointment.confirm': appointments.confirm,
    'appointment.cancel': appointments.cancel,
    'consent.request': records.request_consent,
    'consent.grant': records.grant_consent,
    'consent.revoke': records.revoke_consent,
    'agenda.save': records.save_agenda,
    'rm.share_summary': records.share_agenda,
    'compliance.update_checklist_item': records.update_checklist,
    'requests.create': records.create_request,
    'document.register': records.register_document,
    'document.submit': records.submit_document,
}
