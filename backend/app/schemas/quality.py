"""Payloads for the admin dashboard (services/quality.py, api/admin.py)."""
from datetime import datetime
from pydantic import BaseModel


class Check(BaseModel):
    key: str
    score: float | None
    comment: str=''


class Trace(BaseModel):
    run_id: str
    started_at: datetime
    latency_s: float | None=None
    status: str | None=None
    tokens: int | None=None
    mode: str | None=None
    error: str | None=None


class Span(BaseModel):
    name: str
    run_type: str
    depth: int
    latency_s: float | None=None
    status: str | None=None
    tokens: int | None=None
    agent: str | None=None
    tool: str | None=None
    error: str | None=None
