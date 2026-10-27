"""Card executor contract. The state transition runs in actions.execute_card_block (migration 0019).

PostgreSQL keeps the synthetic card update, receipt, audit and outbox atomic while the backend
connects using the customer's JWT, without a privileged database credential.
"""
from app.schemas.actions import CardBlockTarget
from app.schemas.contracts import ActionCommand
from app.schemas.disputes import DisputeTarget

def temporary_block(command: ActionCommand) -> CardBlockTarget:
    return CardBlockTarget.model_validate(command.target)


def dispute(command: ActionCommand) -> DisputeTarget:
    """Charges and card only; SQL derives the stored items (actions.dispute_target, migration 0066)."""
    return DisputeTarget.model_validate(command.target)
