from fastapi import FastAPI,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from starlette.exceptions import HTTPException

from app.api import actions, auth, chat, consent, conversations, knowledge, notifications, relationship
from app.core.config import settings
from app.core.errors import http_error
from app.core.gateway import GatewayMiddleware
from app.core.lifecycle import lifespan
from app.core.observability import RequestIdMiddleware, setup_logging

setup_logging()
app = FastAPI(title="Chatbot API",lifespan=lifespan)
app.add_exception_handler(HTTPException, http_error)
# Last added runs first: CORS (so error responses keep CORS headers), then request id, then the gateway.
app.add_middleware(GatewayMiddleware)
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(consent.router)
app.include_router(knowledge.router)
app.include_router(relationship.router)
app.include_router(conversations.router)
app.include_router(actions.router)
app.include_router(notifications.router)


@app.get("/health")
def health(request: Request):
    task=getattr(request.app.state,'notification_worker',None)
    if task is not None and task.done():return JSONResponse({'status':'degraded','notification_worker':'stopped'},status_code=503)
    return {"status": "ok"}
