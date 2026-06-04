# configs/constants.py  一些配置，朴素版

import os 
from pathlib import Path 


# 本地 embedding 模型路径
qwen3_embedding_06b_path: str = r"/mnt/e/local_models/embedding/Qwen--Qwen3-Embedding-0.6B"
assert Path(qwen3_embedding_06b_path).is_dir(), "qwen3_embedding_06b_path is not a directory !!! " 


# cloud api 
deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", None)
assert deepseek_api_key is not None, "deepseek_api_key is None !!! "
dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", None)
assert dashscope_api_key is not None, "dashscope_api_key is None !!! "