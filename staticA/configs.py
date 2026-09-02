# staticA/configs.py

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# =======================================================================
#  project constants
# =======================================================================

# project_root            root
project_root: Path=Path(__file__).resolve().parents[1]
assert project_root.is_dir(), f"project_root: {project_root} is not exists!!!"

# env_path                 path 
env_path: Path=project_root / ".env"
assert env_path.is_file(), f"env_path: {env_path} is not exists!!!"

# pipelines_rag/staticA/   root
staticA_root: Path = project_root / "staticA"
assert staticA_root.is_dir(), f"staticA_root: {staticA_root} is not exists!!!"

# knowledge_root           root
knowledge_root: Path = project_root / "knowledge"
assert knowledge_root.is_dir(), f"knowledge_root: {knowledge_root} is not exists!!!"

# project_progress_root    root
project_progress_root: Path = knowledge_root / "project_progress"
assert project_progress_root.is_dir(), f"project_progress_root: {project_progress_root} is not exists!!!"

# texts_root               root
texts_root_v1: Path = project_progress_root / "texts" / "v1"
assert texts_root_v1.is_dir(), f"texts_root_v1: {texts_root_v1} is not exists!!!"
texts_root_v2: Path = project_progress_root / "texts" / "v2"
assert texts_root_v2.is_dir(), f"texts_root_v2: {texts_root_v2} is not exists!!!"
texts_root_v3: Path = project_progress_root / "texts" / "v3"
assert texts_root_v3.is_dir(), f"texts_root_v3: {texts_root_v3} is not exists!!!"
texts_root_v4: Path = project_progress_root / "texts" / "v4"
assert texts_root_v4.is_dir(), f"texts_root_v4: {texts_root_v4} is not exists!!!"

# embeddings_model_name     list
emnedding_model_name: list(str) = ["qwen3_embedding_06b", "gte_embedding_base", "gte_embedding_large"]


class Settings(BaseSettings):
    dashscope_api_key: str = Field(default="", description="DashScope API key (VLM captioning via compatible-mode)")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key (query/eval LLM, optional caption backend)")
    mineru_api_key: str = Field(default="", description="MinerU API key (file parse)")
    langsmith_api_key: str = Field(default="", description="LangSmith API key")
    qwen3_embedding_06b_path: str = Field(default="", description="Local Qwen--Qwen3-embedding-0.6B model path ")
    gte_embedding_base_path: str = Field(default="", description="Local iic--gte-embedding-base  model path ")
    gte_embedding_large_path: str = Field(default="", description="Local iic--gte-embedding-large model path ")
    
    model_config = SettingsConfigDict(
        env_file=env_path,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
