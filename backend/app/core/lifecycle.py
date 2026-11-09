"""API lifecycle: the in-process MCP tool client, and the explicit opt-in workers. A failed worker is visible in health
and propagates on shutdown."""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app import worker
from app.core.config import settings
from app.core.observability import log_event


def report_worker_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():return
    error=task.exception()
    log_event('notification_worker_stopped',logging.ERROR,error_type=type(error).__name__ if error is not None else 'UnexpectedWorkerExit')


@asynccontextmanager
async def notification_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.notification_worker=None
    if not settings.run_worker_in_api:
        yield
        return
    async with worker.connect(worker.database_url()) as db:
        await worker.enqueue_reminders(db)
        task=asyncio.create_task(worker.run(db,settings.worker_poll_seconds),name='notification-worker')
        task.add_done_callback(report_worker_exit)
        app.state.notification_worker=task
        try:yield
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            finally:
                if app.state.notification_worker is task:app.state.notification_worker=None


@asynccontextmanager
async def meeting_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.meeting_worker=None
    if not settings.run_meeting_worker_in_api:
        yield
        return
    if not settings.meetings_enabled or settings.meetings_database_url is None:
        raise RuntimeError('Meeting worker requires MEETINGS_ENABLED and a restricted MEETINGS_DATABASE_URL')
    from app.services.meetings.database import WorkerDatabase
    from app.services.meetings.worker import run
    db=WorkerDatabase(settings.meetings_database_url)
    await asyncio.to_thread(db.verify_identity)
    task=asyncio.create_task(run(db),name='meeting-worker')
    def report_exit(finished: asyncio.Task[None]) -> None:
        if not finished.cancelled():
            error=finished.exception()
            log_event('meeting_worker_stopped',logging.ERROR,error_type=type(error).__name__ if error else 'UnexpectedWorkerExit')
    task.add_done_callback(report_exit)
    app.state.meeting_worker=task
    try:yield
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        finally:app.state.meeting_worker=None


@asynccontextmanager
async def tool_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the event loop's MCP tool client at startup (SDK import, server build: about 2 s cold, measured 8 Nov 2026) so
    the first chat does not wait for it; close it at shutdown."""
    from app import mcp_server
    await mcp_server.client()
    try:yield
    finally:await mcp_server.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with tool_lifespan(app),meeting_lifespan(app),notification_lifespan(app):yield
