"""Typed record-action inputs; private SQL executors commit changes and receipts atomically."""
from app.schemas.agendas import AgendaSaveTarget
from app.schemas.sharing import AgendaShareTarget
from app.schemas.checklist import ChecklistTarget
from app.schemas.request_actions import RequestTarget
from app.schemas.tickets import TicketTarget
from app.schemas.document_uploads import DocumentUploadTarget
from app.schemas.prechecks import SubmitDocumentTarget
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


def update_checklist(command: ActionCommand) -> ChecklistTarget:
    return ChecklistTarget.model_validate(command.target)


def create_request(command: ActionCommand) -> RequestTarget:
    target=RequestTarget.model_validate(command.target)
    return RequestTarget.model_validate({**target.model_dump(),'what':redact(target.what)[0]})


def register_document(command: ActionCommand) -> DocumentUploadTarget:
    return DocumentUploadTarget.model_validate(command.target)


def submit_document(command: ActionCommand) -> SubmitDocumentTarget:
    return SubmitDocumentTarget.model_validate(command.target)


def create_ticket(command: ActionCommand) -> TicketTarget:
    """Redact the customer's own words before SQL stores them; SQL refuses anything still unredacted."""
    target=TicketTarget.model_validate(command.target)
    return TicketTarget.model_validate({**target.model_dump(),'summary':redact(target.summary)[0],
        'details':{key:redact(value)[0] for key,value in target.details.items()}})
