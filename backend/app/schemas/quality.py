"""Payloads for the team-only Quality page (services/quality.py)."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class Check(BaseModel):
    key: str
    score: float | None
    comment: str=''


class Experiment(BaseModel):
    name: str
    dataset: str
    started_at: datetime
    pipeline: str | None=None
    model: str | None=None
    answer_prompt: str | None=None
    judge_model: str | None=None
    url: str | None=None


class Case(BaseModel):
    case_id: str | None
    status: Literal['pass','fail','skipped','error']
    checks: list[Check]
    answer: str=''
    agents: dict[str,int]={}
    tools: list[str]=[]
    model_calls: int | None=None
    latency_s: float | None=None
    tokens: int | None=None
    error: str | None=None
    run_id: str


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
