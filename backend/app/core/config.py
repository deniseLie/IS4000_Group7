
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / '.env')
load_dotenv(Path(__file__).resolve().parents[3] / '.env')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_tokens: int = 4096
    cors_origins: list[str] = ["http://localhost:5173"]

    prompt_version: str = "v2"
    prompt_versions: dict[str, str] = {}
    llm_price_in_per_m: float = 0
    llm_price_out_per_m: float = 0

    # LLM Gateway limits (services/llm_gateway.py)
    llm_timeout_s: float = 30
    llm_max_retries: int = 1
    llm_request_token_budget: int = 24000


settings = Settings()
