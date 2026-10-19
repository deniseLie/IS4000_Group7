"""Executor contracts. SQL's private actions.executors registry owns transactional dispatch."""
from collections.abc import Callable
from app.schemas.actions import CustomerActionType,CardBlockTarget,HandoffTarget
from app.schemas.contracts import ActionCommand
from app.schemas.appointments import AppointmentTarget,SlotTarget
from app.services.actions import appointments,cards,handoffs

EXECUTORS: dict[CustomerActionType,Callable[[ActionCommand],CardBlockTarget | HandoffTarget | SlotTarget | AppointmentTarget]] = {
    'card.temporary_block': cards.temporary_block,
    'handoff.create': handoffs.create,
    'appointment.hold': appointments.hold,
    'appointment.confirm': appointments.confirm,
    'appointment.cancel': appointments.cancel,
}
