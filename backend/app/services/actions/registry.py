"""Executor contracts. SQL's private actions.executors registry owns transactional dispatch."""
from collections.abc import Callable
from app.schemas.actions import CustomerActionType,InputTarget
from app.schemas.contracts import ActionCommand
from app.services.actions import appointments,cards,handoffs,records

EXECUTORS: dict[CustomerActionType,Callable[[ActionCommand],InputTarget]] = {
    'card.temporary_block': cards.temporary_block,
    'handoff.create': handoffs.create,
    'appointment.hold': appointments.hold,
    'appointment.confirm': appointments.confirm,
    'appointment.cancel': appointments.cancel,
    'consent.request': records.request_consent,
    'consent.grant': records.grant_consent,
    'consent.revoke': records.revoke_consent,
    'agenda.save': records.save_agenda,
}
