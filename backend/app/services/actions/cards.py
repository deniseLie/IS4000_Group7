"""STUB card executor (architecture §5.7.3). The real version owns the card state transitions in banking.cards."""
from app.schemas.contracts import ActionCommand

STUB = True


def temporary_block(command: ActionCommand, tx=None) -> dict:
    return {'card_id': command.target.get('card_id'), 'status': 'temporarily_blocked'}
