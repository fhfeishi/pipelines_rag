# Launch Charter: Image-Index RAG

## Why This Exists

Technical PDFs often hide critical information in screenshots, tables, diagrams, and workflow figures. Text-only RAG loses that evidence. Query-time multimodal RAG is expensive and hard to scale because many images may be retrieved per question.

This project tests a more practical design:

```text
Vision at ingestion, text at retrieval.
```

Images are captioned once during indexing, then retrieved as text evidence during normal RAG.

## Pipeline

```text
PDF
-> OpenDataLoader layout parse
-> ordered text/image elements
-> conservative image filtering
-> VLM caption with section title and nearby text
-> text_only / inline_caption / separate_caption / separate_mixed chunks
-> Chroma indexes
-> hybrid BM25 + vector retrieval
-> answer with text and image-caption evidence
```

Main entry points:

- `uv run -m rag_langchain.ingest_img`
- `uv run -m rag_langchain.query_img`

## Success Criteria

- A single PDF can be parsed, captioned, chunked, indexed, and queried end to end.
- `text_only`, `inline`, and `separate` strategies are comparable under the same embedding model, chunk settings, top-k, and answer model.
- `separate` can independently retrieve `image_caption` chunks with `image_id`, `page`, and `image_path`.
- Dry-run and resume flows preserve real caption caches.
- Filtering reduces obvious noise without dropping load-bearing tables, diagrams, or screenshots.
- Logs capture real commands, outputs, failures, and decisions.

## Current Boundaries

- Parser and layout handling are experimental and isolated under `rag_langchain/pdf_*`.
- Query currently uses hybrid BM25 + vector fusion; cross-encoder reranking is not yet the main path.
- The evaluation dataset is still missing systematic labels.
- BM25 tokenization is simple and should be revisited for Chinese-heavy corpora.

## Document Contract

- `README.md` is the concise user manual: install, usage, outputs, caption strategy, validation, boundaries.
- `AGENTS.md` is the canonical agent development guide.
- `CLAUDE.md` is a three-line compatibility shim pointing to `AGENTS.md`.
- `strata.md` is this launch charter.
- `rag_langchain/plan.md` is the active technical blueprint.
- `rag_langchain/logs.md` is append-only run and maintenance history.
- `rag_langchain/notes.md` stores research notes and longer background material.

Agents should keep this contract stable so planning, execution, testing, iteration, summary, and knowledge capture do not drift into competing documents.
