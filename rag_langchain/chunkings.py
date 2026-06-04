"""文本切分（Chunking）实现。

两种策略，均从零手写，不依赖 LangChain：
  1. recursive_chunking  — 多级分隔符递归切分，支持重叠
  2. semantic_chunking  — 基于 embedding 相似度的语义断点切分

参考论文 / 概念：
  - RecursiveCharacterTextSplitter (LangChain)
  - SemanticChunker (LangChain)
  - naive RAG pipeline 中 chunk_size 一般取 256~1024 tokens
"""

from __future__ import annotations

import re
from typing import Callable, Literal, Sequence

import numpy as np
import numpy.typing as npt


# ============================================================================
# 公用的 sentence 切分 / merge 工具
# ============================================================================

# 中英文句子结束符；先按段落（\\n\\n）拆，再在这些分隔符处断句
_SENTENCE_SEP_PATTERN = re.compile(
    r"(?<=[。！？.!?\n])\s*"
)

# 英文句号后面跟空格再接大写字母时不开拆，避免 "Mr. Smith" 误断
# 但中文常见场景下影响不大，这里保留基础实现


def split_sentences(text: str) -> list[str]:
    """将文本切分为句子列表（中英文混排）。"""
    # 先按连续换行拆成段落
    paragraphs = re.split(r"\n\s*\n", text)
    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # 在每个句子结束符后切开
        parts = _SENTENCE_SEP_PATTERN.split(para)
        for p in parts:
            p = p.strip()
            if p:
                sentences.append(p)
    return sentences


def merge_sentences(sentences: Sequence[str], sep: str = "") -> str:
    """将句子列表合并回连续文本。"""
    return sep.join(sentences)


# ============================================================================
# 1. Recursive Chunking — 多级分隔符递归切分
# ============================================================================
#
# 思路：
#   定义一组优先级从高到低的分隔符。对输入文本尝试按最高优先级分隔符拆分；
#   若某片段仍超长，递归使用次优先级分隔符；最终兜底按字符硬截断。
#
# 分隔符优先级（从粗到细）：
#   \\n\\n  → 段落
#   \\n     → 自然行
#   。      → 中文句号
#   .  !  ? → 英文句末（后跟空格或结尾）
#   ；;     → 分号
#   ，,     → 逗号
#   （空格） → 按词（英文）
#   ""      → 按字符
# ============================================================================

# 每个元素是 (分隔符, 是否保留分隔符在片段中)
_SEPARATORS: list[tuple[str, bool]] = [
    ("\n\n", False),               # 段落
    ("\n",   False),               # 换行
    ("。",   True),                # 中文句号（保留，避免句子不完整）
    (".",    True),                # 英文句号
    ("！",   True),                # 中文感叹号
    ("!",    True),                # 英文感叹号
    ("？",   True),                # 中文问号
    ("?",    True),                # 英文问号
    ("；",   True),                # 中文分号
    (";",    True),                # 英文分号
    ("，",   True),                # 中文逗号
    (",",    True),                # 英文逗号
    (" ",    False),               # 空格（按词）
    ("",     False),               # 最终按字符
]


def _split_by_sep(text: str, sep: str, keep_sep: bool) -> list[str]:
    """按分隔符拆分，可选是否把分隔符接在上一段的末尾。"""
    if sep == "":
        return list(text)                  # 按字符

    parts = text.split(sep)
    if not keep_sep:
        return [p for p in parts if p]     # 去掉空串

    # 保留分隔符 → 拼接回每段末尾
    result: list[str] = []
    for i, part in enumerate(parts):
        if i < len(parts) - 1:
            result.append(part + sep)
        elif part:
            result.append(part)
    return result


def _recursive_split(
    text: str,
    seps: list[tuple[str, bool]],
    chunk_size: int,
    start_sep_idx: int = 0,
) -> list[str]:
    """递归核心：对 text 尝试用当前及后续分隔符拆分，保证每段 ≤ chunk_size。"""
    if len(text) <= chunk_size:
        return [text] if text else []

    for idx in range(start_sep_idx, len(seps)):
        sep, keep = seps[idx]
        splits = _split_by_sep(text, sep, keep)

        # 若该分隔符没有产生任何拆分效果（整个 text 就是一段），
        # 直接跳到下一级分隔符，避免无限递归
        if len(splits) <= 1 and sep != "":
            continue

        # 对每个子片段递归处理
        good: list[str] = []
        for s in splits:
            if len(s) <= chunk_size:
                if s:
                    good.append(s)
            else:
                # 当前分隔符切不动 → 用更细粒度的分隔符
                good.extend(_recursive_split(s, seps, chunk_size, idx + 1))
        return good

    # 兜底：按字符硬切
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _apply_overlap(chunks: list[str], chunk_overlap: int) -> list[str]:
    """在相邻 chunk 之间引入重叠区域。

    重叠逻辑：
      chunk[i] 的尾部 overlap 字符作为 chunk[i+1] 的前缀拼接在前面，
      形成 "前文重叠 + 新内容" 的结构。
    """
    if chunk_overlap <= 0 or len(chunks) <= 1:
        return chunks

    result: list[str] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            result.append(chunk)
            continue
        prev = chunks[i - 1]
        # 取前一个 chunk 的后 chunk_overlap 个字符作为上下文前缀
        overlap_text = prev[-chunk_overlap:] if len(prev) > chunk_overlap else prev
        result.append(overlap_text + chunk)
    return result


def recursive_chunking(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[str]:
    """多级分隔符递归切分（RecursiveCharacterTextSplitter 的等价实现）。

    工作流程：
      1. 按 \\n\\n 切段落 → 按 \\n 切行 → 按句末标点切句子
         → 按逗号分号切子句 → 按空格切词 → 按字符硬截断
      2. 对相邻 chunk 施加 overlap，保证语义不中断

    Args:
        text:            待切分文本。
        chunk_size:      每个 chunk 的最大字符数。
        chunk_overlap:   相邻 chunk 的重叠字符数。

    Returns:
        chunk 列表，每个长度 ≤ chunk_size（overlap 前缀可能会略超，但核心内容受控）。
    """
    if not text or not text.strip():
        return []

    # 清理多余的连续空格 / 制表符
    text = re.sub(r"[ \t]+", " ", text)

    chunks = _recursive_split(text, _SEPARATORS, chunk_size)
    chunks = [c.strip() for c in chunks if c.strip()]
    chunks = _apply_overlap(chunks, chunk_overlap)
    return chunks


# ============================================================================
# 2. Semantic Chunking — 基于 embedding 相似度的语义断点
# ============================================================================
#
# 思路：
#   1. 把文本切成句子
#   2. 对每个句子做 embedding
#   3. 计算相邻句子的余弦相似度
#   4. 在相似度陡降处（低于阈值）切分
#   5. 合并断点间的句子，形成语义 chunk
#
# 阈值策略有两种：
#   a. fixed_threshold：低于固定值就切（如 0.5）
#   b. percentile：相似度低于全部分布的第 P 百分位就切（更自适应）
# ============================================================================

def _compute_breakpoints(
    similarities: Sequence[float],
    *,
    threshold: float | None = None,
    percentile: float | None = None,
) -> list[int]:
    """根据相邻句子相似度序列，找到断点索引。

    - 若提供 threshold: 相似度 < threshold 的位置即为断点
    - 若提供 percentile: 先算分布的第 p 百分位阈值，再应用

    Returns:
        断点索引列表（断点在 i 和 i+1 之间，返回 i）。
    """
    if not similarities:
        return []

    sims = np.asarray(similarities, dtype=np.float32)

    if percentile is not None:
        threshold = float(np.percentile(sims, percentile))
    elif threshold is None:
        threshold = 0.5  # 默认阈值

    breakpoints: list[int] = []
    for i, s in enumerate(sims):
        if s < threshold:
            breakpoints.append(i)

    return breakpoints


def _sentences_to_chunks(
    sentences: list[str],
    breakpoints: list[int],
    max_chunk_sentences: int = 10,
) -> list[str]:
    """将句子按断点合并为 chunk，并限制每个 chunk 最大句子数。"""
    if not sentences:
        return []

    chunks: list[str] = []
    start = 0
    bp_set = set(breakpoints)

    for i in range(len(sentences)):
        is_break = i in bp_set
        is_too_long = (i - start + 1) >= max_chunk_sentences

        if is_break or is_too_long or i == len(sentences) - 1:
            end = i + 1
            chunk_text = merge_sentences(sentences[start:end])
            if chunk_text.strip():
                chunks.append(chunk_text)
            start = i + 1   # 下一段从下一句开始

    return chunks


# lazy import 以允许该模块不依赖 embeddings.py
_embed_fn: Callable[[Sequence[str]], npt.NDArray[np.float32]] | None = None


def _get_sentence_embeddings(sentences: list[str]) -> npt.NDArray[np.float32]:
    """获取句子的 embedding（惰性加载 embedding 模型）。

    优先级：相对导入（同 package 内） → 绝对导入（项目根目录挂 PYTHONPATH） → 报错。
    """
    global _embed_fn
    if _embed_fn is not None:
        return _embed_fn(sentences)

    # 多种导入路径兼容 — 因为用户可能 python xxx.py 直接跑，也可能 -m 跑
    encode = None
    for import_path in (
        ".embeddings",                       # from .embeddings import encode  (python -m)
        "scripts_pys.embeddings",            # 项目根目录在 sys.path 中
        "embeddings",                        # 当前目录是 scripts_pys/
    ):
        try:
            mod = __import__(import_path, fromlist=["encode"])
            encode = getattr(mod, "encode", None)
            if encode is not None:
                break
        except (ImportError, ModuleNotFoundError):
            continue

    if encode is None:
        raise ImportError(
            "semantic_chunking 需要 scripts_pys.embeddings 模块。"
            " 请在项目根目录以 python -m scripts_pys.chunkings 方式运行，"
            " 或手动传入 embed_fn。"
        )

    _embed_fn = encode
    return encode(sentences)


def semantic_chunking(
    text: str,
    *,
    threshold: float | None = None,
    percentile: float | None = 30.0,
    max_chunk_sentences: int = 10,
    embed_fn: Callable[[Sequence[str]], npt.NDArray[np.float32]] | None = None,
) -> list[str]:
    """基于 embedding 相似度的语义切分。

    流程：
      1. 将文本拆为句子
      2. 编码每个句子为向量
      3. 计算相邻句子余弦相似度
      4. 在相似度陡降处切分为 chunk

    Args:
        text:                 待切分文本。
        threshold:            固定相似度阈值（低于此值则切分）。
                              None 时自动使用 percentile 策略。
        percentile:           百分位阈值，默认 30，即相似度低于全部分布的第 30 百分位时才切。
        max_chunk_sentences:  单个 chunk 最多包含的句子数（兜底，防止 chunk 过长）。
        embed_fn:             自定义 embedding 函数，签名 f(sentences) -> ndarray。
                              不传则自动从 scripts_pys.embeddings 惰性加载。

    Returns:
        语义 chunk 列表。
    """
    if not text or not text.strip():
        return []

    # 1. 分句
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return [text.strip()]

    # 2. 编码
    if embed_fn is not None:
        global _embed_fn
        _embed_fn = embed_fn

    embeddings = _get_sentence_embeddings(sentences)

    # 3. 计算相邻句子余弦相似度
    from sentence_transformers.util import cos_sim

    # a[i] 是句子 i 和 i+1 的相似度
    sim_matrix = cos_sim(embeddings[:-1], embeddings[1:])
    # cos_sim 返回 (n, m)，这里 n = len-1, m = len-1，取对角线
    if sim_matrix.shape[0] == sim_matrix.shape[1]:
        similarities = sim_matrix.diagonal().numpy().tolist()
    else:
        # fallback: 直接算 dot
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normalized = embeddings / norms
        similarities = (normalized[:-1] * normalized[1:]).sum(axis=1).tolist()

    # 4. 找断点
    breakpoints = _compute_breakpoints(
        similarities,
        threshold=threshold,
        percentile=percentile if threshold is None else None,
    )

    # 5. 合并为 chunk
    chunks = _sentences_to_chunks(sentences, breakpoints, max_chunk_sentences)
    return chunks


# ============================================================================
# Chunking 统一入口
# ============================================================================

def chunking(
    text: str,
    mode: Literal["recursive", "semantic"] = "recursive",
    **kwargs,
) -> list[str]:
    """统一的 chunking 入口。

    Args:
        text: 待切分文本。
        mode: "recursive" 或 "semantic"。
        **kwargs: 传递给对应函数（recursive_chunking 或 semantic_chunking）。
    """
    if mode == "recursive":
        return recursive_chunking(text, **kwargs)
    elif mode == "semantic":
        return semantic_chunking(text, **kwargs)
    else:
        raise ValueError(f"不支持的 mode: {mode!r}，可选 'recursive' / 'semantic'")


# ============================================================================
# 验证 Demo
# ============================================================================

_DEMO_TEXT = """深度学习是机器学习的一个分支，它使用多层神经网络来学习数据的表示。

近年来，Transformer 架构在自然语言处理领域取得了巨大成功。BERT、GPT 等模型在各种 NLP 任务上刷新了记录。

与此同时，计算机视觉领域也涌现了大量基于卷积神经网络和 Vision Transformer 的创新工作。

Python 是一门简洁优雅的编程语言，广泛应用于数据分析、Web 开发和人工智能领域。

在构建 RAG（检索增强生成）系统时，文本切分是一个关键步骤。合理的 chunk 大小能显著提升检索质量。"""


def demo() -> None:
    """运行两种 chunking 策略并对比输出。"""
    # ---------- Recursive ----------
    print("=" * 60)
    print("1. Recursive Chunking (chunk_size=200, overlap=30)")
    print("=" * 60)
    rc = recursive_chunking(_DEMO_TEXT, chunk_size=200, chunk_overlap=30)
    for i, c in enumerate(rc):
        print(f"\n--- chunk [{i}]  len={len(c)} ---")
        print(c)

    # ---------- Semantic ----------
    print("\n\n" + "=" * 60)
    print("2. Semantic Chunking (percentile=30)")
    print("=" * 60)
    try:
        sc = semantic_chunking(_DEMO_TEXT, percentile=30)
    except ImportError as e:
        print(f"[跳过] 无法加载 embedding 模型: {e}")
        return

    for i, c in enumerate(sc):
        print(f"\n--- chunk [{i}]  len={len(c)} ---")
        print(c)


if __name__ == "__main__":
    demo()
