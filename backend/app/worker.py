"""Restricted notification worker: python -m app.worker (WORKER_DATABASE_URL is private).

Provision a dedicated LOGIN role as a member of demobank_worker after applying migrations.
An administrator provisions it with CREATE ROLE <private_worker_login> LOGIN PASSWORD
<private_password>; GRANT demobank_worker TO <private_worker_login>. Supply that login's
connection URL only as WORKER_DATABASE_URL. Passwords and this URL never belong in Git.
Never use the postgres owner, service-role key, or an app JWT. Each function call commits
delivery, event outcome and audit together; a crash rolls back the uncommitted event lock.
SKIP LOCKED excludes competing workers. Expected event failures persist a bounded retry or
dead-letter outcome; unknown SQL/programming failures propagate and stop this worker.
The standalone scheduler calls the idempotent recorded-deadline producer every minute.
It enqueues one reminder per case/due-date/day, from today through seven days ahead in
Asia/Singapore, only while checklist items remain outstanding. Email and worker-started
AI turns are not implemented. Templates currently cover en; missing locales fail explicitly.
"""
import asyncio
import logging
from collections.abc import AsyncIterator,Awaitable,Callable
from contextlib import asynccontextmanager
from typing import TypeVar
from psycopg import AsyncConnection,OperationalError
from psycopg.errors import DeadlockDetected,SerializationFailure
from pydantic import BaseModel,ConfigDict,JsonValue,SecretStr,StrictBool,StrictInt,TypeAdapter
from app.core.config import settings
from app.core.observability import log_event,setup_logging
from app.schemas.notifications import ProcessResult

WorkerRow=tuple[JsonValue,...]
T=TypeVar('T')


class WorkerPermissionError(PermissionError):
    """The supplied database login has more access than this worker requires."""


class WorkerIdentity(BaseModel):
    model_config=ConfigDict(extra='forbid')
    member: StrictBool
    privileged: StrictBool
    table_access: StrictBool


async def database_call(operation: Callable[[],Awaitable[T]]) -> T:
    for attempt in range(3):
        try:return await operation()
        except (OperationalError,DeadlockDetected,SerializationFailure) as error:
            log_event('worker_database_retry',logging.WARNING,attempt=attempt+1,error_type=type(error).__name__,db_code=error.sqlstate)
            if attempt==2:raise
            await asyncio.sleep(0.5*2**attempt)
    raise RuntimeError('Worker retry loop ended without a result')


@asynccontextmanager
async def connect(uri: SecretStr) -> AsyncIterator[AsyncConnection[WorkerRow]]:
    async with await database_call(lambda:AsyncConnection.connect(uri.get_secret_value(),autocommit=True,connect_timeout=5,
        application_name='demobank_notification_worker',options='-c statement_timeout=15000 -c lock_timeout=5000')) as db:
        cursor=await db.execute("select jsonb_build_object('member',pg_has_role(current_user,'demobank_worker','USAGE'),"
            "'privileged',current_user<>session_user or exists(select 1 from pg_roles p "
            "where pg_has_role(current_user,p.oid,'MEMBER') and (p.rolsuper or p.rolbypassrls or p.rolcreaterole or p.rolcreatedb or p.rolreplication)),"
            "'table_access',exists(select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname in ('events','notify','banking','compliance','rm','consent','conversation','actions','support','audit') "
            "and c.relkind in ('r','p') and has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE'))) from pg_roles r where rolname=current_user")
        row=await cursor.fetchone()
        if row is None:raise WorkerPermissionError('Worker database identity could not be verified')
        identity=WorkerIdentity.model_validate(row[0])
        if not identity.member or identity.privileged or identity.table_access:
            raise WorkerPermissionError('WORKER_DATABASE_URL must use an unprivileged login that inherits demobank_worker, with no direct outbox or inbox table privileges')
        yield db


async def process_once(db: AsyncConnection[WorkerRow]) -> ProcessResult | None:
    async def execute() -> WorkerRow | None:
        cursor=await db.execute('select events.process_next_notification()')
        return await cursor.fetchone()
    row=await database_call(execute)
    if row is None:raise RuntimeError('Worker process function returned no result row')
    if row[0] is None:return None
    result=ProcessResult.model_validate(row[0])
    log_event('worker_event',logging.WARNING if result.error_code else logging.INFO,event_id=result.event_id,
        status=result.status,attempts=result.attempts,disposition=result.disposition,error_code=result.error_code)
    return result


async def enqueue_reminders(db: AsyncConnection[WorkerRow]) -> int:
    async def execute() -> WorkerRow | None:
        cursor=await db.execute('select events.enqueue_due_reminders()')
        return await cursor.fetchone()
    row=await database_call(execute)
    if row is None:raise RuntimeError('Reminder producer returned no result row')
    return TypeAdapter(StrictInt).validate_python(row[0])


async def run(db: AsyncConnection[WorkerRow],poll_seconds: float) -> None:
    loop=asyncio.get_running_loop()
    next_schedule=loop.time()
    while True:
        if loop.time()>=next_schedule:
            await enqueue_reminders(db)
            next_schedule=loop.time()+60
        result=await process_once(db)
        if result is None:await asyncio.sleep(poll_seconds)


def database_url() -> SecretStr:
    uri=settings.worker_database_url
    if uri is None or not uri.get_secret_value().strip():raise ValueError('Set private WORKER_DATABASE_URL to a restricted demobank_worker member login before starting the worker')
    return uri


async def main() -> None:
    async with connect(database_url()) as db:await run(db,settings.worker_poll_seconds)


if __name__=='__main__':
    setup_logging()
    asyncio.run(main())
