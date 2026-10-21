"""Typed appointment executor inputs; atomic booking transitions live in migration 0034."""
from app.schemas.appointments import AppointmentTarget,SlotTarget
from app.schemas.contracts import ActionCommand


def hold(command: ActionCommand) -> SlotTarget:
    return SlotTarget.model_validate(command.target)


def confirm(command: ActionCommand) -> AppointmentTarget:
    return AppointmentTarget.model_validate(command.target)


def cancel(command: ActionCommand) -> AppointmentTarget:
    return AppointmentTarget.model_validate(command.target)
