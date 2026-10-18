from fastapi import APIRouter, Depends, Request, Response
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.schemas.contracts import RequestContext
from app.services.audit import AuditWriteError, start_batch

router=APIRouter()

@router.post('/auth/session-end',status_code=204)
async def session_end(request:Request,ctx:RequestContext=Depends(get_ctx)):
    """Called by the client just before sign-out. Records auth.session_ended on the customer's trail."""
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in.')
    batch=start_batch(request.state.db,session_id=ctx.session_id)
    batch.add('auth','auth.session_ended',details={'reason':'sign_out'})
    try:await batch.flush()
    except AuditWriteError:raise ApiError(503,'audit_unavailable','Sign-out could not be recorded. Please retry.')
    return Response(status_code=204)
