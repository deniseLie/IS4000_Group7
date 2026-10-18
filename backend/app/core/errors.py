"""Stable error codes (API Gateway Layer, architecture §5.3). Every error body is {"error": {"code", "message", "request_id"}}.
Clients branch on code, never on message text."""
from fastapi import HTTPException  # FastAPI re-raises this subclass from body reading, so the size limit can raise it
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.observability import request_id

CODES={400:'bad_request',401:'auth_invalid',403:'forbidden',404:'not_found',405:'method_not_allowed',413:'payload_too_large',429:'rate_limited',503:'unavailable'}


class ApiError(HTTPException):
    def __init__(self,status,code,message,headers=None):
        super().__init__(status,message,headers)
        self.code=code


def error_response(exc:StarletteHTTPException):
    code=getattr(exc,'code',None) or CODES.get(exc.status_code,'error')
    return JSONResponse({'error':{'code':code,'message':str(exc.detail),'request_id':request_id.get()}},exc.status_code,headers=exc.headers)


async def http_error(request,exc):return error_response(exc)
