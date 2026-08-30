"""CLI for the deterministic TXT -> Document -> chunk -> Chroma pipeline.

Examples (run from the repository root):

    .venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py audit
    .venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py index
    .venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py query \
        "黄金输变电工程主体结构封顶什么时候完成？"

This entry point intentionally stops at retrieval; it never invokes a chat model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from text_retrieval import (
    DEFAULT_COLLECTION_NAME,
    DEFAULT_CORPUS_ROOT,
    DEFAULT_CORPUS_VERSION,
    DEFAULT_INDEX_ROOT,
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    ChunkConfig,
    EmbeddingConfig,
    audit_text_corpus,
    build_embeddings,
    build_or_reuse_chroma,
    chunk_statistics,
    evaluate_retriever,
    load_eval_cases,
    load_txt_documents,
    open_chroma,
    read_index_manifest,
    retrieve,
    retrieve_hybrid,
    split_documents,
)


DEFAULT_EVAL_PATH = (
    DEFAULT_CORPUS_ROOT.parent / "evals" / "retrieval_v4.jsonl"
)


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _resolved_version(value: str) -> str | None:
    return None if value.casefold() == "all" else value


def _embedding_config(args: argparse.Namespace) -> EmbeddingConfig:
    return EmbeddingConfig(
        backend=args.embedding_backend,
        model=args.embedding_model,
        source=args.embedding_source,
        device=args.device,
        normalize_embeddings=args.normalize_embeddings,
        batch_size=args.batch_size,
        cache_root=args.cache_root,
        revision=args.revision,
        trust_remote_code=args.trust_remote_code,
        base_url=args.embedding_base_url,
        api_key_env=args.api_key_env,
        dimensions=args.embedding_dimensions,
    )


def _chunk_config(args: argparse.Namespace) -> ChunkConfig:
    return ChunkConfig(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )


def _add_corpus_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=DEFAULT_CORPUS_ROOT,
        help=f"TXT corpus root (default: {DEFAULT_CORPUS_ROOT})",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_CORPUS_VERSION,
        help="Corpus snapshot such as v4; use 'all' only for historical comparison",
    )
    parser.add_argument("--chunk-size", type=int, default=872)
    parser.add_argument("--chunk-overlap", type=int, default=160)


def _add_index_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=DEFAULT_INDEX_ROOT,
        help=f"Persistent Chroma directory (default: {DEFAULT_INDEX_ROOT})",
    )
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)


def _add_embedding_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embedding-backend",
        choices=("local", "openai"),
        default="local",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_LOCAL_EMBEDDING_MODEL,
        help="Local repository path, hub model id, or OpenAI-compatible model name",
    )
    parser.add_argument(
        "--embedding-source",
        choices=("local", "huggingface", "modelscope"),
        default="local",
        help="Used only by the local embedding backend",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--normalize-embeddings",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--embedding-base-url", default=None)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--embedding-dimensions", type=int, default=None)


def _add_retrieval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--strategy",
        choices=("dense", "hybrid"),
        default="hybrid",
        help="Dense baseline or dense + Chinese BM25 RRF (default: hybrid)",
    )
    parser.add_argument(
        "--fetch-k",
        type=int,
        default=20,
        help="Candidate count per channel before hybrid fusion",
    )
    parser.add_argument(
        "--dense-weight",
        type=float,
        default=0.40,
        help="Dense contribution to hybrid RRF (default: 0.40, calibrated on v4)",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit", help="Inspect encodings, versions, duplicates, Documents, and chunks"
    )
    _add_corpus_args(audit_parser)

    index_parser = subparsers.add_parser("index", help="Build or reuse the Chroma index")
    _add_corpus_args(index_parser)
    _add_index_args(index_parser)
    _add_embedding_args(index_parser)
    index_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Reset only the named collection when its manifest does not match",
    )

    query_parser = subparsers.add_parser("query", help="Run retrieval without generation")
    query_parser.add_argument("query")
    query_parser.add_argument("-k", type=int, default=4)
    query_parser.add_argument(
        "--source",
        default=None,
        help="Optional exact source metadata filter",
    )
    _add_index_args(query_parser)
    _add_embedding_args(query_parser)
    _add_retrieval_args(query_parser)

    eval_parser = subparsers.add_parser("eval", help="Evaluate retrieval over JSONL")
    eval_parser.add_argument("--dataset", type=Path, default=DEFAULT_EVAL_PATH)
    eval_parser.add_argument("-k", type=int, default=4)
    _add_index_args(eval_parser)
    _add_embedding_args(eval_parser)
    _add_retrieval_args(eval_parser)
    return parser.parse_args()


def _validate_query_contract(
    index_dir: Path,
    collection_name: str,
    embedding_config: EmbeddingConfig,
) -> dict[str, Any]:
    manifest = read_index_manifest(index_dir)
    if manifest is None:
        raise FileNotFoundError(
            f"No index manifest in {index_dir}. Run the 'index' command first."
        )
    stored_embedding = manifest.get("embedding", {})
    stored_fingerprint = stored_embedding.get("fingerprint")
    if stored_fingerprint != embedding_config.fingerprint:
        raise RuntimeError(
            "Query embedding configuration differs from the indexed vectors: "
            f"stored={stored_fingerprint}, requested={embedding_config.fingerprint}."
        )
    if manifest.get("collection_name") != collection_name:
        raise RuntimeError(
            "Collection differs from the index manifest: "
            f"stored={manifest.get('collection_name')!r}, requested={collection_name!r}."
        )
    return manifest


def run_audit(args: argparse.Namespace) -> None:
    version = _resolved_version(args.version)
    documents = load_txt_documents(args.corpus_root, version=version)
    chunks = split_documents(documents, _chunk_config(args))
    payload = audit_text_corpus(args.corpus_root)
    payload["selected_snapshot"] = version or "all"
    payload["selected_document_count"] = len(documents)
    payload["selected_chunk_statistics"] = chunk_statistics(chunks)
    payload["sample_document"] = {
        "id": documents[0].id,
        "metadata": documents[0].metadata,
    }
    payload["sample_chunk"] = {
        "id": chunks[0].id,
        "metadata": chunks[0].metadata,
        "text": chunks[0].page_content,
    }
    _json_print(payload)


def run_index(args: argparse.Namespace) -> None:
    version = _resolved_version(args.version)
    chunk_config = _chunk_config(args)
    embedding_config = _embedding_config(args)
    documents = load_txt_documents(args.corpus_root, version=version)
    chunks = split_documents(documents, chunk_config)
    embeddings = build_embeddings(embedding_config)
    result = build_or_reuse_chroma(
        documents,
        chunks,
        embeddings,
        embedding_config,
        persist_directory=args.index_dir,
        corpus_root=args.corpus_root,
        version=version,
        chunk_config=chunk_config,
        collection_name=args.collection,
        rebuild=args.rebuild,
    )
    _json_print(
        {
            "status": "reused" if result.reused else "built",
            "index_dir": result.persist_directory,
            "collection": result.collection_name,
            "document_count": len(documents),
            "chunk_count": result.document_count,
            "index_fingerprint": result.fingerprint,
            "chunk_statistics": chunk_statistics(chunks),
        }
    )


def _open_validated_store(args: argparse.Namespace):
    embedding_config = _embedding_config(args)
    manifest = _validate_query_contract(
        args.index_dir,
        args.collection,
        embedding_config,
    )
    embeddings = build_embeddings(embedding_config)
    store = open_chroma(
        args.index_dir,
        embeddings,
        collection_name=args.collection,
    )
    actual_count = int(store._collection.count())
    expected_count = int(manifest.get("chunk_count", -1))
    if actual_count != expected_count:
        raise RuntimeError(
            f"Index count does not match manifest: stored={actual_count}, "
            f"manifest={expected_count}"
        )
    return store


def run_query(args: argparse.Namespace) -> None:
    store = _open_validated_store(args)
    metadata_filter = {"source": args.source} if args.source else None
    if args.strategy == "hybrid":
        if metadata_filter:
            raise ValueError("--source filtering is currently supported by dense strategy only")
        hits = retrieve_hybrid(
            store,
            args.query,
            k=args.k,
            fetch_k=args.fetch_k,
            dense_weight=args.dense_weight,
        )
    else:
        hits = retrieve(store, args.query, k=args.k, metadata_filter=metadata_filter)
    _json_print(
        {
            "query": args.query,
            "k": args.k,
            "strategy": args.strategy,
            "hits": [
                {
                    "rank": hit.rank,
                    "score": round(hit.score, 6),
                    "distance": (
                        round(hit.distance, 6) if hit.distance is not None else None
                    ),
                    "dense_rank": hit.document.metadata.get("dense_rank"),
                    "lexical_rank": hit.document.metadata.get("lexical_rank"),
                    "lexical_score": hit.document.metadata.get("lexical_score"),
                    "id": hit.document.id,
                    "source": hit.document.metadata.get("source"),
                    "title": hit.document.metadata.get("title"),
                    "chunk_index": hit.document.metadata.get("chunk_index"),
                    "text": hit.document.page_content,
                }
                for hit in hits
            ],
        }
    )


def run_eval(args: argparse.Namespace) -> None:
    store = _open_validated_store(args)
    report = evaluate_retriever(
        store,
        load_eval_cases(args.dataset),
        k=args.k,
        strategy=args.strategy,
        fetch_k=args.fetch_k,
        dense_weight=args.dense_weight,
    )
    _json_print(report)


def main() -> None:
    args = parse_args()
    handlers = {
        "audit": run_audit,
        "index": run_index,
        "query": run_query,
        "eval": run_eval,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
