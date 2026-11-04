"""Typed talking-point executor inputs; delete, mute, resume and share run in SQL (migration 0108)."""
from app.schemas.contracts import ActionCommand
from app.schemas.talking_points import InsightPointTarget,InsightResumeTarget,InsightShareTarget,InsightTopicTarget


def delete(command: ActionCommand) -> InsightPointTarget:
    return InsightPointTarget.model_validate(command.target)


def mute(command: ActionCommand) -> InsightTopicTarget:
    return InsightTopicTarget.model_validate(command.target)


def resume(command: ActionCommand) -> InsightResumeTarget:
    return InsightResumeTarget.model_validate(command.target)


def share(command: ActionCommand) -> InsightShareTarget:
    return InsightShareTarget.model_validate(command.target)
