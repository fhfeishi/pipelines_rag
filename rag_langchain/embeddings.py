# scripts_pys/embeddings.py


"""本地 embedding 模型加载与使用。

方案:
  a. sentence_transformers（当前方案，开箱即用，API 简洁）  <----> from langchain_huggingface import HuggingFaceEmbeddings
  b. transformers.AutoModel + mean pooling（更底层，适合定制逻辑）
  c. fastembed（轻量，ONNX 推理，CPU 友好）
  d. Text Embeddings Inference（HuggingFace Docker 服务，生产环境）

当前使用方案 a，本地模型路径通过 resolve_local_model_path 自动适配 WSL/Windows。
"""

from __future__ import annotations
# postponed evaluation of annotations，也就是“延迟求值注解”。它告诉解释器，不要在导入时立即求值这些注解，而是在运行时才求值。

from pathlib import Path
from typing import Sequence
# Sequence: 有序序列类型， 常见的有：list, tuple, str, range, bytes, etc.

import numpy as np
import numpy.typing as npt

# a. sentence_transformers  --开箱即用，API 简洁
from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim 
from langchain_huggingface import HuggingFaceEmbeddings

# b. transformers.AutoModel + mean pooling  --更底层，适合定制逻辑
# import torch
# from transformers import AutoTokenizer, AutoModel

# c. fastembed  --轻量，ONNX 推理，CPU 友好
# from fastembed import TextEmbedding

# d. Text Embeddings Inference   --huggingface   docker-service

# # # python rag_langchain/embeddings.py, 会让sys.path[0] 变成 proj_root/rag_langchain
# import sys 
# print("sys.path: ", sys.path)
# # solution 
# # (proj_root) $ python -m rag_langchain.embeddings 会让sys.path[0] 保持 proj_root
# # (proj_root) $ uv run -m rag_langchain.embeddings 会让sys.path[0] 保持 proj_root


from configs.constants import qwen3_embedding_06b_path  # ModuleNotFoundError: No module named 'configs'
# from constants import qwen3_embedding_06b_path  # ModuleNotFoundError: No module named 'constants'
# solution:
# (proj_root) $ python -m rag_langchain.embeddings
# (proj_root) $ uv run -m rag_langchain.embeddings


MODEL_PATH = qwen3_embedding_06b_path

def resolve_local_model_path(path: str) -> Path:
    """将 Windows 路径（E:\\...）转为当前系统可用的本地目录。

    - WSL   下 E:\\foo  →  /mnt/e/foo
    - Linux 下原样返回
    - 路径不存在时抛出 FileNotFoundError
    """
    candidate = Path(path)
    if candidate.is_dir():
        return candidate.resolve()

    # E:\\foo\\bar  →  /mnt/e/foo/bar
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].replace("\\", "/").lstrip("/")
        wsl_path = Path(f"/mnt/{drive}") / rest
        if wsl_path.is_dir():
            return wsl_path.resolve()

    raise FileNotFoundError(
        f"本地模型目录不存在: {path!r}。WSL 下请确认路径类似 /mnt/e/local_models/..."
    )


# ---------------------------------------------------------------------------
# 模型加载（惰性单例）
# ---------------------------------------------------------------------------
_embed_model: SentenceTransformer | None = None
def load_embed_model(path: str = MODEL_PATH) -> SentenceTransformer:
    """加载本地 embedding 模型（惰性单例，只加载一次）。"""
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    local_path = resolve_local_model_path(path)
    # SentenceTransformer 方式
    _embed_model = SentenceTransformer(
        str(local_path),
        local_files_only=True,       # 强制本地，不联网
        device="cpu",                # 可按需改为 "cuda"
    )
    
    # # HuggingFaceEmbeddings 方式
    # _embed_model = HuggingFaceEmbeddings(
    #     model_name=str(local_path),
    #     model_kwargs={
    #         "device": "cpu",
    #         "local_files_only": True,
    #     },
    #     encode_kwargs={
    #         "normalize_embeddings": True,
    #     },
    # )
    
    return _embed_model


# 模块级懒加载：首次 import 不加载模型，首次调用 encode() 才加载
embed_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global embed_model
    if embed_model is None:
        embed_model = load_embed_model()
    return embed_model


# ---------------------------------------------------------------------------
# 核心 API
# ---------------------------------------------------------------------------

def encode(
    sentences: str | Sequence[str],
    *,
    normalize: bool = True,
    show_progress: bool = False,
    batch_size: int = 32,
) -> npt.NDArray[np.float32]:
    """将文本编码为 embedding 向量。

    Args:
        sentences: 单个句子或句子列表。
        normalize: 是否做 L2 归一化（推荐开启，方便直接做点积/余弦相似度）。
        show_progress: 是否显示进度条。
        batch_size: 批大小。

    Returns:
        (n, dim) 或 (dim,) 的 float32 数组。
    """
    model = _get_model()

    input_is_str = isinstance(sentences, str)
    if input_is_str:
        sentences = [sentences]

    embeddings = model.encode(
        sentences,                               # type: ignore[arg-type]
        normalize_embeddings=normalize,
        show_progress_bar=show_progress,
        batch_size=batch_size,
        convert_to_numpy=True,
    )

    if input_is_str:
        return embeddings[0]                     # type: ignore[return-value]
    return embeddings                            # type: ignore[return-value]


def similarity(a: npt.NDArray, b: npt.NDArray) -> npt.NDArray[np.float32]:
    """计算两组 embedding 之间的余弦相似度矩阵。

    等价于 sklearn.metrics.pairwise.cosine_similarity(a, b)。
    """
    t = cos_sim(a, b)
    return t.numpy().astype(np.float32)


def top_k(
    query_emb: npt.NDArray,
    corpus_embs: npt.NDArray,
    k: int = 5,
) -> list[tuple[int, float]]:
    """返回与 query 最相似的 top-k 个 corpus 索引及分数。

    Returns:
        [(corpus_index, similarity_score), ...]，按分数降序。
    """
    sims = similarity(query_emb.reshape(1, -1), corpus_embs)[0]
    top_indices = np.argsort(sims)[::-1][:k]
    return [(int(i), float(sims[i])) for i in top_indices]


# ---------------------------------------------------------------------------
# 便捷使用示例
# ---------------------------------------------------------------------------

def demo() -> None:
    """验证模型加载及编码 / 相似度计算。"""
    print("加载模型 ...")
    model = _get_model()
    print(f"  模型已就绪: {model}")

    sentences = [
        "你好世界",
        "这是一段关于机器学习的测试文本",
        "机器学习是人工智能的一个重要分支",
        "今天天气真不错",
        "Python 是一种非常流行的编程语言",
    ]

    # 1. 编码
    print("\n编码句子 ...")
    embs = encode(sentences)
    print(f"  shape: {embs.shape}, dtype: {embs.dtype}")

    # 2. 单句编码
    single_emb = encode("你好世界")
    print(f"  单句 shape: {single_emb.shape}")

    # 3. 相似度矩阵
    print("\n余弦相似度矩阵:")
    sim_matrix = similarity(embs, embs)
    for i, row in enumerate(sim_matrix):
        print(f"  [{i}] {sentences[i][:25]:25s} | {' '.join(f'{v:.2f}' for v in row)}")

    # 4. Top-K 检索
    print("\n最接近 '人工智能' 的句子:")
    query_emb = encode("人工智能")
    results = top_k(query_emb, embs, k=3)
    for idx, score in results:
        print(f"  {sentences[idx]:40s}  score={score:.4f}")


if __name__ == "__main__":
    demo()
