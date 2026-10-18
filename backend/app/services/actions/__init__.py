"""STUB Action Service (architecture §5.7.3): run_action() with the agreed signature and sample receipts.
The real version adds expiry, a Postgres transaction, the outbox row and the audit event; nothing is persisted here."""
from datetime import timedelta
from app.schemas.contracts import ActionCommand, ProposedCommand, Receipt, RequestContext, now
from app.services.policy import decide

STUB = True
CONFIRM_WINDOW = timedelta(minutes=5)
_receipts: dict[str, Receipt] = {}  # idempotency_key -> receipt, in memory for the stub only


async def run_action(command: ActionCommand, ctx: RequestContext, confirmed: bool = False) -> Receipt | ProposedCommand:
    from app.services.actions.registry import EXECUTORS  # lazy: executors import this package
    if command.idempotency_key in _receipts:
        return _receipts[command.idempotency_key]
    decision = decide(ctx.principal, command.type, command.target, ctx)
    if not decision.allow:
        raise PermissionError(command.type)
    if 'require:confirmation' in decision.obligations and not confirmed:
        return ProposedCommand(command_id=command.command_id, expires_at=now() + CONFIRM_WINDOW)
    if command.type not in EXECUTORS:
        raise ValueError(f'unknown action type {command.type}')
    EXECUTORS[command.type](command)
    receipt = Receipt(receipt_id=f'rcpt_{command.command_id.hex[:12]}', command_id=command.command_id, outcome='succeeded')
    _receipts[command.idempotency_key] = receipt
    return receipt
