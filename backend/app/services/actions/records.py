"""Typed record-action inputs; consent state changes run atomically in migration 0037."""
from app.schemas.consent_actions import ConsentGrantTarget,ConsentRequestTarget,ConsentRevokeTarget
from app.schemas.contracts import ActionCommand


def request_consent(command: ActionCommand) -> ConsentRequestTarget:
    return ConsentRequestTarget.model_validate(command.target)


def grant_consent(command: ActionCommand) -> ConsentGrantTarget:
    return ConsentGrantTarget.model_validate(command.target)


def revoke_consent(command: ActionCommand) -> ConsentRevokeTarget:
    return ConsentRevokeTarget.model_validate(command.target)
