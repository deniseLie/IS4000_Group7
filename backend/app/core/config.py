
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
    llm_price_in_per_m: float = 0
    llm_price_out_per_m: float = 0

    # API Gateway Layer (core/gateway.py)
    jwt_audience: str = "authenticated"
    jwks_ttl_s: int = 600
    max_body_bytes: int = 65536  # the client bounds history to 16000 characters
    rate_capacity: int = 30  # burst per signed-in user
    rate_refill_per_s: float = 1.0
    guest_rate_capacity: int = 20  # burst per client address
    guest_rate_refill_per_s: float = 0.5


settings = Settings()
