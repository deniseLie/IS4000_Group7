
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from pathlib import Path
from typing import Literal
from pydantic import Field,SecretStr

load_dotenv(Path(__file__).resolve().parents[2] / '.env')
load_dotenv(Path(__file__).resolve().parents[3] / '.env')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_tokens: int = 4096
    pipeline: Literal['v1', 'v2'] = 'v1'
    # Knowledge search (services/kb.py, migration 0099): keyword ranks fused with embedding similarity. The embedding client
    # has its own key and endpoint, never LLM_API_KEY; without EMBEDDING_API_KEY, or when the provider fails, search is keyword only.
    knowledge_respell: bool = True  # typos.respell before search; off measures search without the spelling table
    embedding_api_key: SecretStr | None = Field(default=None,repr=False)
    embedding_base_url: str = 'https://openrouter.ai/api/v1'
    embedding_model: str = Field(default='openai/text-embedding-3-small',pattern=r'^[a-z0-9][a-z0-9._/:-]{0,99}$')
    embedding_min_similarity: float = Field(default=0.4,ge=-1,le=1)  # a section found only by similarity must reach this (tuned 3 Nov 2026)
    embedding_timeout_s: float = Field(default=5,gt=0,le=30)
    conversation_store: Literal['off','database'] = 'off'
    client_intent_routing: Literal['adaptive','rules'] = 'adaptive'  # LLM-first for ordinary client intents; critical deterministic safeguards remain
    worker_poll_seconds: float = Field(default=2,gt=0,le=60)
    run_worker_in_api: bool = False
    meetings_enabled: bool = False  # meeting lifecycle (services/meetings, 0105); explicit opt-in for the hybrid meeting lifecycle
    meeting_model_token_budget: int = 200000
    zoom_oauth_directory: Path | None = None
    zoom_capture_enabled: bool = False
    zoom_webhook_secret: SecretStr | None = Field(default=None,repr=False)
    zoom_ingress_database_url: SecretStr | None = Field(default=None,repr=False)
    meetings_database_url: SecretStr | None = Field(default=None,repr=False)
    meetings_checkpoint_signing_key: SecretStr | None = Field(default=None,min_length=43,repr=False)
    run_meeting_worker_in_api: bool = False
    meeting_import_max_bytes: int = Field(default=2200000,ge=65536,le=8000000)
    document_precheck_signing_key: SecretStr | None = Field(default=None,repr=False)
    document_ocr_command: str | None = None
    precheck_mode: Literal['template','notice'] = 'template'  # template: fixed per-document rules (document_templates.py); notice: model reads the notice
    document_ocr_languages: str=Field(default="eng",pattern=r"^[A-Za-z0-9_+.-]{1,80}$")
    document_ocr_vision_languages: str=Field(default="en-US",pattern=r"^[A-Za-z0-9,_.-]{1,100}$")
    document_ocr_word_confidence: float=Field(default=80,ge=0,le=100)
    document_ocr_mean_confidence: float=Field(default=90,ge=0,le=100)
    worker_database_url: SecretStr | None = Field(default=None,repr=False)
    cors_origins: list[str] = ["http://localhost:5173"]
    # Prompt versions are not settings: services/prompts.VERSIONS is the one table.

    # LLM Gateway limits (services/llm_gateway.py)
    llm_timeout_s: float = 30
    llm_max_retries: int = 1
    llm_request_token_budget: int = 24000
    agent_timeout_s: float = Field(default=65,gt=0,le=300)
    # v5 agent runner (services/agents/runner.py): tool calls per `open` task, and the whole-turn deadline (phase 5).
    agent_tool_calls: int = Field(default=4,ge=1,le=8)
    turn_budget_s: float = Field(default=60,gt=0,le=300)
    planner_model: str | None = None  # v5 planner (phase 5); unset uses LLM_MODEL
    # Talking points (0108, lane 1): the classifier's model; unset uses the agent model (LLM_MODEL), so a small or
    # self-hosted model can be chosen. Same provider, key and base URL.
    talking_points_model: str | None = None  # an empty value also means LLM_MODEL
    # Meeting agents (Brief Writer, Minutes Writer, Proposal Drafter) send strict nested JSON schemas ($defs, anyOf,
    # const, null). Some providers reject those; set a model that supports them (e.g. openai/gpt-4.1-mini on
    # OpenRouter). Unset or empty uses LLM_MODEL. Same provider, key and base URL.
    meeting_model: str | None = None

    # API Gateway Layer (core/gateway.py)
    jwt_audience: str = "authenticated"
    jwks_ttl_s: int = 600
    max_body_bytes: int = 65536  # the client bounds history to 16000 characters
    rate_capacity: int = 30  # burst per signed-in user
    rate_refill_per_s: float = 1.0
    guest_rate_capacity: int = 20  # burst per client address
    guest_rate_refill_per_s: float = 0.5


settings = Settings()
