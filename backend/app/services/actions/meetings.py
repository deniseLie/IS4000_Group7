"""Typed meeting-request executor inputs; the request, withdrawal and counter reply run in SQL (migration 0106)."""
from app.schemas.contracts import ActionCommand
from app.schemas.meeting_requests import CounterReplyTarget,MeetingRefTarget,MeetingRequestTarget


def request(command: ActionCommand) -> MeetingRequestTarget:
    return MeetingRequestTarget.model_validate(command.target)


def withdraw(command: ActionCommand) -> MeetingRefTarget:
    return MeetingRefTarget.model_validate(command.target)


def counter_reply(command: ActionCommand) -> CounterReplyTarget:
    return CounterReplyTarget.model_validate(command.target)
