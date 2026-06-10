# Agent Guide

Canonical guide for agents working on `pipelines-rag`.

## Mission

Research how to index images (screenshots / tables / architecture diagrams) from
technical-documentation PDFs so RAG can answer image-helpful / image-required
questions:

```text
PDF -> opendataloader parse -> L1/L2 image filter -> VLM caption -> chunk -> Chroma -> hybrid query
```

Core principle: **Vision at ingestion, text at retrieval.** The VLM reads each
image once at ingest time to produce a caption; the query path is text-only.

## Layout

- `rag_pdfs/` — the canonical package. All new work happens here.
- `rag_langchain/` — frozen historical exploration material. Do not extend it;
  its useful code was already merged into `rag_pdfs` with imports rewritten.
- `configs/` — pydantic-settings config; secrets live in `configs/.env` (gitignored).

## Read Order

1. `rag_pdfs/strata.md` — launch charter (goals, non-goals, success criteria)
2. `rag_pdfs/notes.md` — design notes (blog conclusions + experiment design)
3. `rag_pdfs/plan.md` — engineering blueprint and module map
4. `rag_pdfs/logs.md` — append-only run history
5. Code entry points: `rag_pdfs/ingest_img.py`, `rag_pdfs/query_img.py`, `rag_pdfs/eval_img.py`

## Development Rules

- Canonical CLI entries: `uv run -m rag_pdfs.ingest_img`, `uv run -m rag_pdfs.query_img`, `uv run -m rag_pdfs.eval_img`
- Keep PDF-specific logic behind the `pdf_*` modules so the parser can be swapped later
- Captioning happens only at ingest; never send images to the VLM at query time (experiment group E is the sole upper-bound control)
- API keys come from `configs/.env` (`dashscope_api_key` for VLM captioning, `deepseek_api_key` for query/eval LLM); never commit keys
- Do not reintroduce the video/audio transcript tool removed in repo history

## Validation Rules

Doc-only changes:

```bash
uv run -m rag_pdfs.ingest_img --help
```

Behavior changes require API keys and a sample PDF; run the affected CLI
end-to-end and record results in `rag_pdfs/logs.md`. If keys or samples are
unavailable, still run `--help` and note what could not be verified.

## Documentation Contract

- `README.md` — concise user manual
- `AGENTS.md` — this file
- `CLAUDE.md` — shim pointing to `AGENTS.md`
- `strata.md` — shim pointing to `rag_pdfs/strata.md`
- `rag_pdfs/plan.md` — technical plan
- `rag_pdfs/logs.md` — append-only run log
- `git_notes.md` — git incident notes (history rewrite / recovery); keep for reference

When behavior changes, update `rag_pdfs/plan.md` and append to `rag_pdfs/logs.md`.

## Current Priority

- Quantify inline vs separate caption indexing strategies with the labeled
  question set (experiments A/B/C/D in `rag_pdfs/notes.md`)
- Measure caption quality: section-anchor coverage, `quality_flag`, recall@k
