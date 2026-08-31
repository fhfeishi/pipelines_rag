# staticA/configs/config.py

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from constants import env_file


class Settings(BaseSettings):
    dashscope_api_key: str = Field(default="", description="DashScope API key (VLM captioning via compatible-mode)")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key (query/eval LLM, optional caption backend)")
    mineru_api_key: str = Field(default="", description="MinerU API key (file parse)")
    langsmith_api_key: str = Field(default="", description="LangSmith API key")
    qwen3_embedding_06b_path: str = Field(default="", description="Local Qwen--Qwen3-embedding-0.6B model path ")
    gte_embedding_base_path: str = Field(default="", description="Local iic--gte-embedding-base  model path ")
    gte_embedding_large_path: str = Field(default="", description="Local iic--gte-embedding-large model path ")
    
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
