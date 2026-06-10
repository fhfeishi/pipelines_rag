from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / ".env"


class Settings(BaseSettings):
    dashscope_api_key: str = Field(default="", description="DashScope API key (VLM captioning via compatible-mode)")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key (query/eval LLM, optional caption backend)")
    qwen3_embedding_06b_path: str = Field(default="", description="Local Qwen3 embedding 0.6B model path (rag_pdfs)")

    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
