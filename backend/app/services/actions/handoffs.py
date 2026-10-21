"""Handoff executor contract; atomic deduplication and creation/reuse events live in migration 0019."""
from app.schemas.actions import HandoffTarget
from app.schemas.contracts import ActionCommand
from app.services.privacy import redact

def create(command: ActionCommand) -> HandoffTarget:
    target=HandoffTarget.model_validate(command.target)
    return target.model_copy(update={'summary':redact(target.summary)[0]})
