"""Explicit opt-in API worker lifecycle. A failed worker is visible in health and propagates on shutdown."""
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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
