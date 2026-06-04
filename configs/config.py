# configs/config.py   # wsl侧不适用。
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / ".env"
assert ENV_FILE.exists(), "Environment file not found"

class Settings(BaseSettings):
    deepseek_api_key: str=Field(default='', description="DeepSeek API key")
    dashscope_api_key: str=Field(default='', description="DashScope API key")
    qwen3_embedding_06b_path: str=Field(default='', description="Qwen3 embedding 0.6B path")

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )


settings = Settings()





if __name__ == "__main__":
    print(f"{settings.deepseek_api_key = }")
    print(f"{settings.dashscope_api_key = }")
    print(f"{settings.qwen3_embedding_06b_path = }")