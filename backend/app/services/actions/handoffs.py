"""STUB handoff executor (architecture §5.7.3). The real version writes support.handoffs (open -> claimed -> closed)."""
from app.schemas.contracts import ActionCommand

STUB = True


def create(command: ActionCommand, tx=None) -> dict:
    return {'handoff_id': 'hnd_sample_1', 'priority': command.target.get('priority', 'P3'), 'status': 'open'}
