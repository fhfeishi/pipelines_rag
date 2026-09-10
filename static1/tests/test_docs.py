from pathlib import Path

from src.agent.docs import DocRecord, DocsIndex, build_context


def test_docs_index_loads_and_ranks_cached_records(tmp_path: Path) -> None:
    cache = tmp_path / "docs.json"
    cache.write_text(
        '[{"title":"RAG guide","url":"https://example.test/rag","text":"Build a retriever and use RAG."},'
        '{"title":"Agents","url":"https://example.test/agents","text":"Agents can call tools."}]',
        encoding="utf-8",
    )

    index = DocsIndex(cache, urls=(), max_pages=0)
    results = index.search("retriever RAG")

    assert results[0].title == "RAG guide"
    assert "https://example.test/rag" in build_context(results)


def test_empty_index_does_not_return_results(tmp_path: Path) -> None:
    index = DocsIndex(tmp_path / "missing.json", urls=())

    assert index.search("anything") == []


def test_docs_index_can_search_in_memory_records(tmp_path: Path) -> None:
    index = DocsIndex(tmp_path / "missing.json", urls=())
    index.records = [
        DocRecord(
            title="Streaming",
            url="https://example.test/streaming",
            text="Use streaming to emit tokens progressively.",
        )
    ]

    assert index.search("tokens")
