"""Release API results only after their audit batch is stored."""
from app.core.errors import ApiError
from app.services.audit import AuditBatch,AuditWriteError


async def flush(batch: AuditBatch) -> None:
    try:await batch.flush()
    except AuditWriteError as error:
        raise ApiError(503,'audit_unavailable','The request could not be recorded. Please retry.') from error
