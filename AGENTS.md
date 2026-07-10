# Agent Guide

Canonical guide for agents working on `pipelines-rag`.

## Mission

Build `pipelines-rag` as a long-running Agentic RAG product prototype and
engineering learning ground. The core product problem is how to index images
(screenshots / tables / architecture diagrams) from technical-documentation PDFs
so RAG can answer image-helpful / image-required questions:

```text
PDF -> opendataloader parse -> L1/L2 image filter -> VLM caption -> chunk -> Chroma -> hybrid query
```

Core principle: **Vision at ingestion, text at retrieval.** The VLM reads each
image once at ingest time to produce a caption; the query path is text-only.

Near-term product focus: first make the document parser/tooling layer reliable:
source data -> PDF -> layout JSON + extracted images -> image-aware Markdown.
That Markdown is the human-inspectable bridge between raw parsing and downstream
caption/chunk/index/eval.

## Layout (layered)

```text
Layer 0–1  parsers/     source → StaticParsePackage (document.md, elements.jsonl, …)
Layer 2–3  rag_pdfs/   filter → caption → chunk → Chroma → query → eval
```

- `parsers/` — parser/tooling experiments (Layer 0–1). `redox_*` for PDF;
 `rewebpage_*` / `reaudio_*` for URL and media. Output contract in
 `parsers/plan.md`. **Must not depend on `rag_pdfs`** — enforced since
 2026-07-07: shared parse core lives in `parsers/document/` (layout /
 opendataloader / package); `rag_pdfs.pdf_layout` / `pdf_parser` are now
 compatibility shims re-exporting from it.
- `rag_pdfs/` — canonical image-RAG package (Layer 2–3). New RAG/eval work
  here; prefer consuming Layer 1 parse dirs over duplicating parse logic.
- `rag_langchain/` — frozen historical exploration material. Do not extend it;
  its useful code was already merged into `rag_pdfs` with imports rewritten.
- `configs/` — pydantic-settings config; secrets live in `configs/.env` (gitignored).

## Read Order

1. `strata.md` — repo charter (layers, priorities, known engineering debt)
2. `rag_pdfs/strata.md` — image-RAG product charter
3. `agent_rag_growth_roadmap.md` — product line + agent-learning roadmap
4. `rag_pdfs/notes.md` — design notes (blog conclusions + experiment design)
5. `rag_pdfs/plan.md` — RAG engineering blueprint and module map
6. `parsers/plan.md` — parser toolbox and StaticParsePackage contract
7. `rag_pdfs/logs.md` — append-only run history
8. Code entry points: Layer 1 `python -m parsers.static_structurer`; Layer 2–3
   `uv run -m rag_pdfs.{ingest_img,query_img,eval_img}`

## Development Rules

- Canonical CLI entries: `uv run -m rag_pdfs.ingest_img`, `uv run -m rag_pdfs.query_img`, `uv run -m rag_pdfs.eval_img`
- Keep PDF-specific logic behind the `pdf_*` modules so the parser can be swapped later
- Keep parser adapters behind `parsers/redox_*` until the output contract is stable. Parser tools should preserve source PDF, layout JSON, extracted images, `elements.jsonl`, `images.jsonl`, parse summary, and Markdown with stable relative image links.
- Put parser outputs under an `outputs/<source-stem>/` style directory when practical, matching `parsers/plan.md`.
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
- `strata.md` — repo charter (layers, priorities)
- `rag_pdfs/strata.md` — image-RAG product charter
- `rag_pdfs/plan.md` — technical plan
- `rag_pdfs/logs.md` — append-only run log
- `parsers/plan.md` — parser/tooling notes and output conventions
- `git_notes.md` — git incident notes (history rewrite / recovery); keep for reference

When behavior changes, update `rag_pdfs/plan.md` and append to `rag_pdfs/logs.md`.
When parser-tool behavior changes, update `parsers/plan.md` as well.

## Current Priority

1. Build the PDF/data parser -> image-aware Markdown tool:
   source data or webpage snapshot -> PDF -> opendataloader layout JSON ->
   extracted images -> ordered Markdown with image references and metadata.
2. Use that Markdown as the inspectable staging artifact for `rag_pdfs` ingest,
   so parsing/layout bugs can be fixed before VLM captioning.
3. Then return to quantifying inline vs separate caption indexing strategies
   with the labeled question set (experiments A/B/C/D in `rag_pdfs/notes.md`)
   and caption quality metrics: section-anchor coverage, `quality_flag`,
   recall@k.
