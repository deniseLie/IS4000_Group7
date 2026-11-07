"""Explicitly registered meeting runner for an isolated hosted rehearsal.

Run `CHAT_TRANSPORT=codex CODEX_CLI_TIMEOUT_S=180 python -m app.services.meetings.registered_worker --runtime /private/runtime.json`.
The owner-only JSON contains {"database_url":"...","meeting_ids":["..."]}. No .env is edited. The runtime URL must be
the restricted meeting LOGIN, never an owner/service login. Both queue claims and retention are restricted to those
IDs. Configure the approved OAuth directory separately only when transcript.fetch is deliberately being tested.
This process remains running until stopped; a permanent host/supervisor and non-expiring LOGIN require deployment.
"""
import argparse,asyncio,json,os,stat
from pathlib import Path
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel,ConfigDict,Field,SecretStr
from app.schemas.meeting_lifecycle import Claim
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.worker import run

class Runtime(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,hide_input_in_errors=True)
    database_url: SecretStr=Field(repr=False)
    meeting_ids: tuple[UUID,...]=Field(min_length=1,max_length=20)


def runtime(path: Path) -> Runtime:
    permissions=path.stat()
    if permissions.st_uid!=os.getuid() or not stat.S_ISREG(permissions.st_mode) or permissions.st_mode&0o077:
        raise PermissionError('Registered worker runtime must be a regular owner-only file (chmod 600)')
    return Runtime.model_validate_json(path.read_text())


class RegisteredWorkerDatabase(WorkerDatabase):
    """Restricted SQL connector with explicit meeting membership on every claim and retention call."""
    def __init__(self,configuration: Runtime) -> None:
        super().__init__(configuration.database_url)
        self.meeting_ids=configuration.meeting_ids

    def claim(self) -> Claim|None:
        with psycopg.connect(self.uri.get_secret_value(),row_factory=dict_row) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select * from meetings.claim_registered(%s)',(list(self.meeting_ids),)).fetchone()
            if row is None:raise RuntimeError('Registered queue returned no response row')
            return None if row['event_id'] is None else Claim.model_validate(row)

    def schedule_due(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.schedule_registered_timers(%s)',(list(self.meeting_ids),)).fetchone()
            if row is None:raise RuntimeError('Registered meeting scheduler returned no response')
            return int(row[0])

    def schedule_graph_updates(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.schedule_registered_graphs(%s)',(list(self.meeting_ids),)).fetchone()
            if row is None:raise RuntimeError('Registered graph scheduler returned no response')
            return int(row[0])

    def purge_transcripts(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.purge_registered(%s)',(list(self.meeting_ids),)).fetchone()
            if row is None:raise RuntimeError('Registered retention returned no response row')
            return int(row[0])


if __name__=='__main__':
    from app.core.observability import setup_logging
    setup_logging()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args();asyncio.run(run(RegisteredWorkerDatabase(runtime(args.runtime))))
