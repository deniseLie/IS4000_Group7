
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
    knowledge_store: Literal['memory','database'] = 'memory'
    conversation_store: Literal['off','database'] = 'off'
    tool_transport: Literal['direct','mcp'] = 'direct'  # mcp: agent tool calls go through app/mcp_server.py
    worker_poll_seconds: float = Field(default=2,gt=0,le=60)
    run_worker_in_api: bool = False
    document_precheck_signing_key: SecretStr | None = Field(default=None,repr=False)
    document_ocr_command: str | None = None
    precheck_mode: Literal['template','notice'] = 'template'  # template: fixed per-document rules (document_templates.py); notice: model reads the notice
    document_ocr_languages: str=Field(default="eng",pattern=r"^[A-Za-z0-9_+.-]{1,80}$")
    document_ocr_vision_languages: str=Field(default="en-US",pattern=r"^[A-Za-z0-9,_.-]{1,100}$")
    document_ocr_word_confidence: float=Field(default=80,ge=0,le=100)
    document_ocr_mean_confidence: float=Field(default=90,ge=0,le=100)
    worker_database_url: SecretStr | None = Field(default=None,repr=False)
    cors_origins: list[str] = ["http://localhost:5173"]

    prompt_version: str = "v3"
    prompt_versions: dict[str, str] = {'planner':'v2','domain_common':'v1','retail':'v1','fraud':'v1','compliance':'v1','document':'v1','relationship':'v1'}

    # LLM Gateway limits (services/llm_gateway.py)
    llm_timeout_s: float = 30
    llm_max_retries: int = 1
    llm_request_token_budget: int = 24000
    agent_timeout_s: float = Field(default=65,gt=0,le=300)

    # API Gateway Layer (core/gateway.py)
    jwt_audience: str = "authenticated"
    jwks_ttl_s: int = 600
    max_body_bytes: int = 65536  # the client bounds history to 16000 characters
    rate_capacity: int = 30  # burst per signed-in user
    rate_refill_per_s: float = 1.0
    guest_rate_capacity: int = 20  # burst per client address
    guest_rate_refill_per_s: float = 0.5


settings = Settings()
