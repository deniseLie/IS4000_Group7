
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / '.env')
load_dotenv(Path(__file__).resolve().parents[3] / '.env')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_tokens: int = 4096
    cors_origins: list[str] = ["http://localhost:5173"]

    prompt_version: str = "v1"


settings = Settings()
