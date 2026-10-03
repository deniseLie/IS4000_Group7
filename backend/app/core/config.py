from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 1024
    system_prompt: str = "You are a helpful assistant."
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
