"""Runtime configuration for the personal LangChain docs assistant."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


STATIC1_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-backed settings with safe local defaults."""

    deepseek_api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    docs_request_timeout: float = Field(default=20.0, validation_alias="DOCS_REQUEST_TIMEOUT")
    docs_max_pages: int = Field(default=8, validation_alias="DOCS_MAX_PAGES")
    docs_cache_path: Path = Field(
        default=STATIC1_ROOT / "data" / "langchain_docs.json",
        validation_alias="DOCS_CACHE_PATH",
    )
    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")

    model_config = SettingsConfigDict(
        env_file=(str(STATIC1_ROOT.parent / ".env"), str(STATIC1_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object."""

    return Settings()


__all__ = ["STATIC1_ROOT", "Settings", "get_settings"]
