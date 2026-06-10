from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / ".env"


class Settings(BaseSettings):
    dashscope_api_key: str = Field(default="", description="DashScope API key")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key for polish step")
    polish_model: str = Field(default="deepseek-chat", description="LLM model for transcript polish")
    polish_base_url: str = Field(default="https://api.deepseek.com", description="OpenAI-compatible polish API base URL")

    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
