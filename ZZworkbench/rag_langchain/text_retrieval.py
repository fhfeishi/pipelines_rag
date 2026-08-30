"""Deterministic text ingestion, chunking, indexing, and dense retrieval.

This module owns the small, stable boundary between a curated TXT corpus and
LangChain. Parser-specific code belongs upstream: once text is trustworthy,
the retrieval pipeline only consumes ``langchain_core.documents.Document``.

The module intentionally contains no answer-generation step.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_ROOT = PROJECT_ROOT / "knowledge" / "project_progress" / "texts"
DEFAULT_CORPUS_VERSION = "v4"
DEFAULT_INDEX_ROOT = PROJECT_ROOT / "outputs" / "text_rag" / DEFAULT_CORPUS_VERSION
DEFAULT_COLLECTION_NAME = "project_progress_text"
DEFAULT_LOCAL_EMBEDDING_MODEL = (
    "/mnt/e/local_models/embedding/iic--nlp_gte_sentence-embedding_chinese-large"
)
INDEX_MANIFEST_NAME = "index_manifest.json"
INDEX_SCHEMA_VERSION = 1

TITLE_PATTERN = re.compile(r"该进度计划的完整名称为(?P<title>.+?)[。\n]")
LEXICAL_PATTERN = re.compile(r"[a-z0-9]+(?:[._+#/-][a-z0-9]+)*|[\u3400-\u9fff]+")
CHINESE_SEPARATORS = ("\n\n", "\n", "。", "；", "！", "？", "，", "、", " ", "")
SCALAR_TYPES = (str, int, float, bool)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _stable_hash(payload: Any, *, length: int | None = None) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    value = _sha256_bytes(encoded)
    return value if length is None else value[:length]


def _as_project_source(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _scalar_metadata(metadata: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    """Return metadata accepted by Chroma without silently losing fields."""

    clean: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, SCALAR_TYPES):
            clean[str(key)] = value
        else:
            clean[str(key)] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return clean


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Character-based chunking tuned for paragraph-oriented Chinese records."""

    chunk_size: int = 872
    chunk_overlap: int = 160
    separators: tuple[str, ...] = CHINESE_SEPARATORS

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if not self.separators or self.separators[-1] != "":
            raise ValueError("separators must end with an empty-string fallback")

    def contract(self) -> dict[str, Any]:
        return {
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "separators": list(self.separators),
            "length_function": "python_len",
            "keep_separator": "end",
        }


EmbeddingBackend = Literal["local", "openai"]
LocalModelSource = Literal["local", "huggingface", "modelscope"]


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Secret-free configuration for local or OpenAI-compatible embeddings."""

    backend: EmbeddingBackend = "local"
    model: str = DEFAULT_LOCAL_EMBEDDING_MODEL
    source: LocalModelSource = "local"
    device: str = "cpu"
    normalize_embeddings: bool = True
    batch_size: int = 32
    cache_root: str | None = None
    revision: str | None = None
    trust_remote_code: bool = False
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    dimensions: int | None = None

    def __post_init__(self) -> None:
        if self.backend not in {"local", "openai"}:
            raise ValueError(f"Unsupported embedding backend: {self.backend}")
        if self.source not in {"local", "huggingface", "modelscope"}:
            raise ValueError(f"Unsupported local model source: {self.source}")
        if not self.model.strip():
            raise ValueError("embedding model cannot be empty")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.dimensions is not None and self.dimensions < 1:
            raise ValueError("dimensions must be positive")

    def contract(self) -> dict[str, Any]:
        """Settings that must match between indexing and querying."""

        payload = asdict(self)
        payload.pop("api_key_env", None)
        if self.backend == "openai":
            for key in (
                "source",
                "device",
                "normalize_embeddings",
                "batch_size",
                "cache_root",
                "revision",
                "trust_remote_code",
            ):
                payload.pop(key, None)
        else:
            payload.pop("base_url", None)
            payload.pop("dimensions", None)
        return payload

    @property
    def fingerprint(self) -> str:
        return _stable_hash(self.contract(), length=16)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    document: Document
    rank: int
    score: float
    distance: float | None


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    persist_directory: Path
    collection_name: str
    document_count: int
    fingerprint: str
    reused: bool


def discover_txt_files(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    version: str | None = DEFAULT_CORPUS_VERSION,
) -> list[Path]:
    """Discover TXT files deterministically.

    ``version=None`` explicitly means all version directories. The normal RAG
    path defaults to the latest curated snapshot (currently ``v4``), preventing
    near-duplicate historical versions from crowding retrieval results.
    """

    root = Path(corpus_root).expanduser().resolve()
    search_root = root / version if version else root
    if not search_root.is_dir():
        raise FileNotFoundError(f"Corpus directory does not exist: {search_root}")
    files = sorted(
        (path.resolve() for path in search_root.rglob("*.txt") if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    )
    if not files:
        raise FileNotFoundError(f"No .txt files found under: {search_root}")
    return files


def load_txt_document(
    path: str | Path,
    *,
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
) -> Document:
    """Read one strict UTF-8 TXT file and return a LangChain ``Document``."""

    file_path = Path(path).expanduser().resolve()
    root = Path(corpus_root).expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    raw = file_path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise UnicodeError(f"TXT file is not valid UTF-8: {file_path}") from exc

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError(f"TXT file is empty after normalization: {file_path}")

    try:
        relative_to_corpus = file_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"TXT file must be inside corpus_root: {file_path}") from exc

    version = relative_to_corpus.parts[0] if len(relative_to_corpus.parts) > 1 else ""
    title_match = TITLE_PATTERN.search(text)
    title = title_match.group("title").strip() if title_match else file_path.stem
    source = _as_project_source(file_path)
    source_sha256 = _sha256_bytes(raw)
    document_id = "txt-" + _stable_hash(
        {"source": source, "source_sha256": source_sha256}, length=24
    )
    metadata = _scalar_metadata(
        {
            "document_id": document_id,
            "source": source,
            "source_name": file_path.name,
            "corpus_version": version,
            "title": title,
            "mime_type": "text/plain",
            "encoding": encoding,
            "source_sha256": source_sha256,
            "byte_size": len(raw),
            "char_count": len(text),
            "loader": "strict_utf8_txt",
        }
    )
    return Document(id=document_id, page_content=text, metadata=metadata)


def iter_txt_documents(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    version: str | None = DEFAULT_CORPUS_VERSION,
) -> Iterator[Document]:
    """Lazily yield one ``Document`` per TXT file."""

    for path in discover_txt_files(corpus_root, version=version):
        yield load_txt_document(path, corpus_root=corpus_root)


def load_txt_documents(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    version: str | None = DEFAULT_CORPUS_VERSION,
) -> list[Document]:
    return list(iter_txt_documents(corpus_root, version=version))


def split_documents(
    documents: Iterable[Document],
    config: ChunkConfig = ChunkConfig(),
) -> list[Document]:
    """Split documents and attach deterministic, provenance-rich chunk metadata."""

    splitter = RecursiveCharacterTextSplitter(
        separators=list(config.separators),
        keep_separator="end",
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        length_function=len,
        add_start_index=True,
        strip_whitespace=True,
    )
    chunks: list[Document] = []
    for parent in documents:
        parent_id = parent.id or str(parent.metadata.get("document_id", ""))
        if not parent_id:
            raise ValueError("Every parent Document must have a stable id")

        for chunk_index, raw_chunk in enumerate(splitter.split_documents([parent])):
            start_index = int(raw_chunk.metadata.get("start_index", 0))
            chunk_sha256 = _sha256_text(raw_chunk.page_content)
            chunk_id = "chunk-" + _stable_hash(
                {
                    "document_id": parent_id,
                    "start_index": start_index,
                    "text_sha256": chunk_sha256,
                    "chunking": config.contract(),
                },
                length=24,
            )
            metadata = _scalar_metadata(
                {
                    **parent.metadata,
                    "document_id": parent_id,
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "start_index": start_index,
                    "end_index": start_index + len(raw_chunk.page_content),
                    "chunk_char_count": len(raw_chunk.page_content),
                    "chunk_sha256": chunk_sha256,
                    "chunk_size": config.chunk_size,
                    "chunk_overlap": config.chunk_overlap,
                }
            )
            chunks.append(
                Document(id=chunk_id, page_content=raw_chunk.page_content, metadata=metadata)
            )
    return chunks


def audit_text_corpus(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
) -> dict[str, Any]:
    """Return a read-only corpus inventory, including exact duplicate groups."""

    root = Path(corpus_root).expanduser().resolve()
    files = discover_txt_files(root, version=None)
    versions: dict[str, dict[str, int | bool]] = {}
    hashes: dict[str, list[str]] = {}
    total_bytes = 0
    total_chars = 0

    for path in files:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
            valid_utf8 = True
        except UnicodeDecodeError:
            text = ""
            valid_utf8 = False
        relative = path.relative_to(root)
        version = relative.parts[0] if len(relative.parts) > 1 else ""
        stats = versions.setdefault(
            version,
            {"files": 0, "bytes": 0, "chars": 0, "all_utf8": True},
        )
        stats["files"] = int(stats["files"]) + 1
        stats["bytes"] = int(stats["bytes"]) + len(raw)
        stats["chars"] = int(stats["chars"]) + len(text)
        stats["all_utf8"] = bool(stats["all_utf8"]) and valid_utf8
        total_bytes += len(raw)
        total_chars += len(text)
        hashes.setdefault(_sha256_bytes(raw), []).append(relative.as_posix())

    duplicate_groups = [
        {"sha256": digest, "sources": sources}
        for digest, sources in sorted(hashes.items())
        if len(sources) > 1
    ]
    return {
        "corpus_root": _as_project_source(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_chars": total_chars,
        "versions": dict(sorted(versions.items())),
        "exact_duplicate_groups": duplicate_groups,
    }


def _configure_cache_environment(cache_root: str | None) -> None:
    if not cache_root:
        return
    root = Path(cache_root).expanduser().resolve()
    os.environ.setdefault("HF_HOME", str(root / "huggingface"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(root / "huggingface" / "hub"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(root / "modelscope"))


def build_embeddings(
    config: EmbeddingConfig,
    *,
    api_key: str | None = None,
) -> Embeddings:
    """Instantiate a LangChain embedding backend from a secret-free config."""

    if config.backend == "openai":
        from langchain_openai import OpenAIEmbeddings

        resolved_key = api_key or os.getenv(config.api_key_env, "").strip()
        if not resolved_key:
            raise RuntimeError(
                f"Missing embedding API key: pass api_key or set {config.api_key_env}"
            )
        kwargs: dict[str, Any] = {
            "model": config.model,
            "api_key": resolved_key,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.dimensions is not None:
            kwargs["dimensions"] = config.dimensions
        return OpenAIEmbeddings(**kwargs)

    _configure_cache_environment(config.cache_root)
    model_reference = config.model
    cache_folder: str | None = None
    if config.source == "local":
        local_path = Path(config.model).expanduser().resolve()
        if not local_path.is_dir():
            raise FileNotFoundError(f"Local embedding model not found: {local_path}")
        model_reference = str(local_path)
    elif config.source == "huggingface":
        if config.cache_root:
            cache_folder = str(Path(config.cache_root).expanduser() / "huggingface")
    else:
        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except ImportError as exc:
            raise RuntimeError("ModelScope source requires the modelscope package") from exc
        download_kwargs: dict[str, Any] = {}
        if config.cache_root:
            download_kwargs["cache_dir"] = str(
                Path(config.cache_root).expanduser() / "modelscope"
            )
        if config.revision:
            download_kwargs["revision"] = config.revision
        model_reference = str(snapshot_download(config.model, **download_kwargs))

    from langchain_huggingface import HuggingFaceEmbeddings

    model_kwargs: dict[str, Any] = {
        "device": config.device,
        "trust_remote_code": config.trust_remote_code,
    }
    if config.revision and config.source == "huggingface":
        model_kwargs["revision"] = config.revision
    return HuggingFaceEmbeddings(
        model=model_reference,
        cache_folder=cache_folder,
        model_kwargs=model_kwargs,
        encode_kwargs={
            "normalize_embeddings": config.normalize_embeddings,
            "batch_size": config.batch_size,
        },
        query_encode_kwargs={
            "normalize_embeddings": config.normalize_embeddings,
        },
        show_progress=False,
    )


def make_index_manifest(
    documents: Sequence[Document],
    chunks: Sequence[Document],
    *,
    corpus_root: str | Path,
    version: str | None,
    chunk_config: ChunkConfig,
    embedding_config: EmbeddingConfig,
    collection_name: str,
) -> dict[str, Any]:
    sources = [
        {
            "document_id": document.id,
            "source": document.metadata["source"],
            "source_sha256": document.metadata["source_sha256"],
            "char_count": document.metadata["char_count"],
        }
        for document in documents
    ]
    contract = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "corpus_root": _as_project_source(Path(corpus_root)),
        "corpus_version": version or "all",
        "collection_name": collection_name,
        "sources": sources,
        "chunking": chunk_config.contract(),
        "embedding": {
            **embedding_config.contract(),
            "fingerprint": embedding_config.fingerprint,
        },
        "document_count": len(documents),
        "chunk_count": len(chunks),
    }
    return {
        **contract,
        "index_fingerprint": _stable_hash(contract),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def read_index_manifest(persist_directory: str | Path) -> dict[str, Any] | None:
    path = Path(persist_directory) / INDEX_MANIFEST_NAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid index manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Index manifest must be a JSON object: {path}")
    return payload


def _write_index_manifest(persist_directory: Path, manifest: Mapping[str, Any]) -> None:
    path = persist_directory / INDEX_MANIFEST_NAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def open_chroma(
    persist_directory: str | Path,
    embeddings: Embeddings,
    *,
    collection_name: str = DEFAULT_COLLECTION_NAME,
):
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=str(Path(persist_directory).expanduser().resolve()),
        collection_metadata={"hnsw:space": "cosine"},
    )


def build_or_reuse_chroma(
    documents: Sequence[Document],
    chunks: Sequence[Document],
    embeddings: Embeddings,
    embedding_config: EmbeddingConfig,
    *,
    persist_directory: str | Path = DEFAULT_INDEX_ROOT,
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    version: str | None = DEFAULT_CORPUS_VERSION,
    chunk_config: ChunkConfig = ChunkConfig(),
    collection_name: str = DEFAULT_COLLECTION_NAME,
    rebuild: bool = False,
) -> IndexBuildResult:
    """Build a persistent Chroma collection or reuse an exact manifest match."""

    if not chunks:
        raise ValueError("Cannot build an index with no chunks")
    persist_path = Path(persist_directory).expanduser().resolve()
    persist_path.mkdir(parents=True, exist_ok=True)
    requested = make_index_manifest(
        documents,
        chunks,
        corpus_root=corpus_root,
        version=version,
        chunk_config=chunk_config,
        embedding_config=embedding_config,
        collection_name=collection_name,
    )
    existing = read_index_manifest(persist_path)
    vector_store = open_chroma(
        persist_path,
        embeddings,
        collection_name=collection_name,
    )
    stored_count = int(vector_store._collection.count())

    same_contract = bool(
        existing
        and existing.get("index_fingerprint") == requested["index_fingerprint"]
    )
    if same_contract and stored_count == len(chunks):
        return IndexBuildResult(
            persist_directory=persist_path,
            collection_name=collection_name,
            document_count=stored_count,
            fingerprint=str(requested["index_fingerprint"]),
            reused=True,
        )

    if (existing is not None or stored_count > 0) and not rebuild:
        reason = (
            f"manifest_match={same_contract}, stored={stored_count}, "
            f"requested={len(chunks)}"
        )
        raise RuntimeError(
            f"Existing index does not match the requested corpus/config ({reason}). "
            "Re-run with rebuild=True / --rebuild after reviewing the target path."
        )

    if stored_count > 0 or existing is not None:
        vector_store.reset_collection()
    clean_chunks = [
        Document(
            id=chunk.id,
            page_content=chunk.page_content,
            metadata=_scalar_metadata(chunk.metadata),
        )
        for chunk in chunks
    ]
    vector_store.add_documents(
        documents=clean_chunks,
        ids=[str(chunk.id) for chunk in clean_chunks],
    )
    final_count = int(vector_store._collection.count())
    if final_count != len(clean_chunks):
        raise RuntimeError(
            f"Chroma count mismatch after indexing: expected {len(clean_chunks)}, "
            f"got {final_count}"
        )
    _write_index_manifest(persist_path, requested)
    return IndexBuildResult(
        persist_directory=persist_path,
        collection_name=collection_name,
        document_count=final_count,
        fingerprint=str(requested["index_fingerprint"]),
        reused=False,
    )


def chinese_lexical_tokens(text: str) -> list[str]:
    """Tokenize mixed Chinese/Latin text without an external segmenter.

    Chinese runs become overlapping character bigrams and trigrams. This keeps
    exact project/task names searchable while avoiding the broken
    ``lower().split()`` behavior on Chinese text. Latin identifiers, dates, and
    voltage labels are retained as normalized tokens.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in LEXICAL_PATTERN.finditer(normalized):
        value = match.group(0)
        if "\u3400" <= value[0] <= "\u9fff":
            if len(value) == 1:
                tokens.append(value)
                continue
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
            if len(value) >= 3:
                tokens.extend(
                    value[index : index + 3] for index in range(len(value) - 2)
                )
        else:
            tokens.append(value)
    return tokens


class ChineseBM25Index:
    """Small in-memory lexical index built from the persisted Chroma collection."""

    def __init__(self, documents: Sequence[Document]) -> None:
        from rank_bm25 import BM25Okapi

        self.documents = list(documents)
        tokenized = [
            chinese_lexical_tokens(
                f"{document.metadata.get('title', '')}\n{document.page_content}"
            )
            for document in self.documents
        ]
        self._bm25 = BM25Okapi(tokenized)
        descriptors: dict[str, str] = {}
        for document in self.documents:
            document_id = str(document.metadata.get("document_id", ""))
            if not document_id:
                continue
            descriptors.setdefault(
                document_id,
                f"{document.metadata.get('title', '')} "
                f"{document.metadata.get('source_name', '')}",
            )
        self._descriptor_tokens = {
            document_id: set(chinese_lexical_tokens(descriptor))
            for document_id, descriptor in descriptors.items()
        }
        self._descriptor_df: Counter[str] = Counter(
            token
            for tokens in self._descriptor_tokens.values()
            for token in tokens
        )

    @classmethod
    def from_chroma(cls, vector_store: Any) -> ChineseBM25Index:
        payload = vector_store.get(include=["documents", "metadatas"])
        ids = payload.get("ids") or []
        texts = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        documents = [
            Document(
                id=str(identifier),
                page_content=str(text),
                metadata=_scalar_metadata(metadata or {}),
            )
            for identifier, text, metadata in zip(ids, texts, metadatas, strict=True)
        ]
        return cls(documents)

    def route_document_ids(self, query: str) -> set[str] | None:
        """Route an explicit project/document mention before chunk retrieval."""

        query_tokens = set(chinese_lexical_tokens(query))
        document_count = len(self._descriptor_tokens)
        if not query_tokens or document_count < 2:
            return None
        max_df = max(1, document_count // 3)
        scores: list[tuple[str, float]] = []
        for document_id, descriptor_tokens in self._descriptor_tokens.items():
            distinctive = [
                token
                for token in query_tokens & descriptor_tokens
                if self._descriptor_df[token] <= max_df
            ]
            score = sum(
                math.log((document_count + 1) / (self._descriptor_df[token] + 1)) + 1.0
                for token in distinctive
            )
            scores.append((document_id, score))
        best = max((score for _, score in scores), default=0.0)
        if best <= 0:
            return None
        selected = {
            document_id
            for document_id, score in scores
            if score >= best * 0.8 and score > 0
        }
        return selected or None

    def search(
        self,
        query: str,
        *,
        k: int,
        document_ids: set[str] | None = None,
    ) -> list[tuple[Document, float]]:
        tokens = chinese_lexical_tokens(query)
        if not tokens or not self.documents:
            return []
        scores = self._bm25.get_scores(tokens)
        eligible = (
            (index, score)
            for index, score in enumerate(scores)
            if document_ids is None
            or str(self.documents[index].metadata.get("document_id")) in document_ids
        )
        ranked = sorted(
            eligible,
            key=lambda item: (-float(item[1]), str(self.documents[item[0]].id)),
        )
        return [
            (self.documents[index], float(score))
            for index, score in ranked[:k]
            if float(score) > 0
        ]


class ScoredChromaRetriever(BaseRetriever):
    """LangChain retriever that preserves Chroma cosine distance and score.

    Calling ``invoke`` uses LangChain's retriever callback lifecycle, so setting
    ``LANGSMITH_TRACING=true`` is sufficient for LangSmith retrieval traces.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    vector_store: Any = Field(exclude=True)
    k: int = 4
    metadata_filter: dict[str, Any] | None = None

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        pairs = self.vector_store.similarity_search_with_score(
            query=query,
            k=self.k,
            filter=self.metadata_filter,
        )
        ranked: list[Document] = []
        for rank, (document, raw_distance) in enumerate(pairs, start=1):
            distance = float(raw_distance)
            metadata = _scalar_metadata(
                {
                    **document.metadata,
                    "retrieval_rank": rank,
                    "retrieval_distance": distance,
                    "retrieval_score": 1.0 - distance,
                }
            )
            ranked.append(
                Document(id=document.id, page_content=document.page_content, metadata=metadata)
            )
        return ranked


class HybridChromaRetriever(BaseRetriever):
    """Fuse dense and Chinese BM25 rankings with weighted reciprocal rank."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    vector_store: Any = Field(exclude=True)
    lexical_index: ChineseBM25Index = Field(exclude=True)
    k: int = 4
    fetch_k: int = 20
    dense_weight: float = 0.40
    rrf_constant: int = 60

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        routed_document_ids = self.lexical_index.route_document_ids(query)
        dense_filter: dict[str, Any] | None = None
        if routed_document_ids:
            sorted_ids = sorted(routed_document_ids)
            dense_filter = (
                {"document_id": sorted_ids[0]}
                if len(sorted_ids) == 1
                else {"document_id": {"$in": sorted_ids}}
            )
        dense_pairs = self.vector_store.similarity_search_with_score(
            query=query,
            k=self.fetch_k,
            filter=dense_filter,
        )
        lexical_pairs = self.lexical_index.search(
            query,
            k=self.fetch_k,
            document_ids=routed_document_ids,
        )
        candidates: dict[str, dict[str, Any]] = {}

        for rank, (document, raw_distance) in enumerate(dense_pairs, start=1):
            identity = str(document.id or document.metadata.get("chunk_id"))
            candidates[identity] = {
                "document": document,
                "dense_rank": rank,
                "distance": float(raw_distance),
            }
        for rank, (document, lexical_score) in enumerate(lexical_pairs, start=1):
            identity = str(document.id or document.metadata.get("chunk_id"))
            entry = candidates.setdefault(identity, {"document": document})
            entry["lexical_rank"] = rank
            entry["lexical_score"] = lexical_score

        lexical_weight = 1.0 - self.dense_weight
        for entry in candidates.values():
            dense_rank = entry.get("dense_rank")
            lexical_rank = entry.get("lexical_rank")
            entry["fusion_score"] = (
                self.dense_weight / (self.rrf_constant + dense_rank)
                if dense_rank is not None
                else 0.0
            ) + (
                lexical_weight / (self.rrf_constant + lexical_rank)
                if lexical_rank is not None
                else 0.0
            )

        ordered = sorted(
            candidates.items(),
            key=lambda item: (-float(item[1]["fusion_score"]), item[0]),
        )[: self.k]
        ranked: list[Document] = []
        for rank, (_, entry) in enumerate(ordered, start=1):
            document = entry["document"]
            extra: dict[str, Any] = {
                "retrieval_strategy": "hybrid_rrf",
                "retrieval_rank": rank,
                "retrieval_score": float(entry["fusion_score"]),
                "metadata_routed": bool(routed_document_ids),
            }
            if "dense_rank" in entry:
                extra["dense_rank"] = int(entry["dense_rank"])
                extra["retrieval_distance"] = float(entry["distance"])
                extra["dense_score"] = 1.0 - float(entry["distance"])
            if "lexical_rank" in entry:
                extra["lexical_rank"] = int(entry["lexical_rank"])
                extra["lexical_score"] = float(entry["lexical_score"])
            ranked.append(
                Document(
                    id=document.id,
                    page_content=document.page_content,
                    metadata=_scalar_metadata({**document.metadata, **extra}),
                )
            )
        return ranked


def build_retriever(
    vector_store: Any,
    *,
    k: int = 4,
    metadata_filter: dict[str, Any] | None = None,
) -> ScoredChromaRetriever:
    if k < 1:
        raise ValueError("k must be positive")
    return ScoredChromaRetriever(
        vector_store=vector_store,
        k=k,
        metadata_filter=metadata_filter,
        tags=["project_progress", "dense_retrieval"],
        metadata={"retrieval_strategy": "dense_cosine", "top_k": k},
    )


def build_hybrid_retriever(
    vector_store: Any,
    *,
    k: int = 4,
    fetch_k: int = 20,
    dense_weight: float = 0.40,
) -> HybridChromaRetriever:
    if k < 1:
        raise ValueError("k must be positive")
    if fetch_k < k:
        raise ValueError("fetch_k must be at least k")
    if not 0.0 <= dense_weight <= 1.0:
        raise ValueError("dense_weight must be between 0 and 1")
    return HybridChromaRetriever(
        vector_store=vector_store,
        lexical_index=ChineseBM25Index.from_chroma(vector_store),
        k=k,
        fetch_k=fetch_k,
        dense_weight=dense_weight,
        tags=["project_progress", "hybrid_retrieval"],
        metadata={
            "retrieval_strategy": "hybrid_rrf",
            "top_k": k,
            "fetch_k": fetch_k,
            "dense_weight": dense_weight,
        },
    )


def _documents_to_hits(documents: Sequence[Document]) -> list[RetrievalHit]:
    hits: list[RetrievalHit] = []
    for document in documents:
        raw_distance = document.metadata.get("retrieval_distance")
        hits.append(
            RetrievalHit(
                document=document,
                rank=int(document.metadata["retrieval_rank"]),
                score=float(document.metadata["retrieval_score"]),
                distance=float(raw_distance) if raw_distance is not None else None,
            )
        )
    return hits


def retrieve(
    vector_store: Any,
    query: str,
    *,
    k: int = 4,
    metadata_filter: dict[str, Any] | None = None,
) -> list[RetrievalHit]:
    """Retrieve scored chunks through the standard LangChain retriever lifecycle."""

    documents = build_retriever(
        vector_store,
        k=k,
        metadata_filter=metadata_filter,
    ).invoke(query)
    return _documents_to_hits(documents)


def retrieve_hybrid(
    vector_store: Any,
    query: str,
    *,
    k: int = 4,
    fetch_k: int = 20,
    dense_weight: float = 0.40,
) -> list[RetrievalHit]:
    """Retrieve with dense + Chinese BM25 weighted reciprocal-rank fusion."""

    documents = build_hybrid_retriever(
        vector_store,
        k=k,
        fetch_k=fetch_k,
        dense_weight=dense_weight,
    ).invoke(query)
    return _documents_to_hits(documents)


def load_eval_cases(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}: {path}") from exc
        if not isinstance(row, dict) or not str(row.get("query", "")).strip():
            raise ValueError(f"Eval line {line_number} requires a non-empty query")
        cases.append(row)
    if not cases:
        raise ValueError(f"No evaluation cases found: {path}")
    return cases


def evaluate_retriever(
    vector_store: Any,
    cases: Sequence[Mapping[str, Any]],
    *,
    k: int = 4,
    strategy: Literal["dense", "hybrid"] = "hybrid",
    fetch_k: int = 20,
    dense_weight: float = 0.40,
) -> dict[str, Any]:
    """Measure deterministic source-hit and expected-term coverage at k."""

    active_retriever: BaseRetriever
    if strategy == "hybrid":
        active_retriever = build_hybrid_retriever(
            vector_store,
            k=k,
            fetch_k=fetch_k,
            dense_weight=dense_weight,
        )
    elif strategy == "dense":
        active_retriever = build_retriever(vector_store, k=k)
    else:
        raise ValueError(f"Unknown retrieval strategy: {strategy}")

    rows: list[dict[str, Any]] = []
    for case in cases:
        query = str(case["query"])
        hits = _documents_to_hits(active_retriever.invoke(query))
        expected_source = str(case.get("expected_source", ""))
        expected_terms = [str(term) for term in case.get("expected_terms", [])]
        source_hit = not expected_source or any(
            str(hit.document.metadata.get("source", "")).endswith(expected_source)
            for hit in hits
        )
        term_hit = not expected_terms or any(
            all(term in hit.document.page_content for term in expected_terms)
            for hit in hits
        )
        rows.append(
            {
                "id": case.get("id"),
                "query": query,
                "source_hit": source_hit,
                "term_hit": term_hit,
                "top_sources": [
                    hit.document.metadata.get("source", "") for hit in hits
                ],
            }
        )

    count = len(rows)
    return {
        "case_count": count,
        "k": k,
        "strategy": strategy,
        "source_hit_at_k": sum(bool(row["source_hit"]) for row in rows) / count,
        "term_hit_at_k": sum(bool(row["term_hit"]) for row in rows) / count,
        "rows": rows,
    }


def chunk_statistics(chunks: Sequence[Document]) -> dict[str, Any]:
    lengths = [len(chunk.page_content) for chunk in chunks]
    by_source = Counter(str(chunk.metadata.get("source", "")) for chunk in chunks)
    if not lengths:
        return {"count": 0, "min_chars": 0, "max_chars": 0, "mean_chars": 0.0}
    return {
        "count": len(lengths),
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "mean_chars": round(sum(lengths) / len(lengths), 2),
        "by_source": dict(sorted(by_source.items())),
    }


__all__ = [
    "CHINESE_SEPARATORS",
    "DEFAULT_COLLECTION_NAME",
    "DEFAULT_CORPUS_ROOT",
    "DEFAULT_CORPUS_VERSION",
    "DEFAULT_INDEX_ROOT",
    "DEFAULT_LOCAL_EMBEDDING_MODEL",
    "ChunkConfig",
    "ChineseBM25Index",
    "EmbeddingConfig",
    "HybridChromaRetriever",
    "IndexBuildResult",
    "RetrievalHit",
    "ScoredChromaRetriever",
    "audit_text_corpus",
    "build_embeddings",
    "build_hybrid_retriever",
    "build_or_reuse_chroma",
    "build_retriever",
    "chunk_statistics",
    "chinese_lexical_tokens",
    "discover_txt_files",
    "evaluate_retriever",
    "iter_txt_documents",
    "load_eval_cases",
    "load_txt_document",
    "load_txt_documents",
    "make_index_manifest",
    "open_chroma",
    "read_index_manifest",
    "retrieve",
    "retrieve_hybrid",
    "split_documents",
]
