"""Typed record-action inputs; private SQL executors commit changes and receipts atomically."""
from app.schemas.agendas import AgendaSaveTarget
from app.schemas.sharing import AgendaShareTarget
from app.schemas.consent_actions import ConsentGrantTarget,ConsentRequestTarget,ConsentRevokeTarget
from app.schemas.contracts import ActionCommand
from app.services.privacy import redact


def request_consent(command: ActionCommand) -> ConsentRequestTarget:
    return ConsentRequestTarget.model_validate(command.target)


def grant_consent(command: ActionCommand) -> ConsentGrantTarget:
    return ConsentGrantTarget.model_validate(command.target)


def revoke_consent(command: ActionCommand) -> ConsentRevokeTarget:
    return ConsentRevokeTarget.model_validate(command.target)


def save_agenda(command: ActionCommand) -> AgendaSaveTarget:
    target=AgendaSaveTarget.model_validate(command.target)
    return AgendaSaveTarget.model_validate({**target.model_dump(),'topics':[redact(topic)[0] for topic in target.topics]})


def share_agenda(command: ActionCommand) -> AgendaShareTarget:
    return AgendaShareTarget.model_validate(command.target)
