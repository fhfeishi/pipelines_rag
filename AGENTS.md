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

## Layout

- `rag_pdfs/` — the canonical package. All new work happens here.
- `parsers/` — parser/tooling experiments. `redox_*` handles document/PDF
  parsing into JSON/Markdown/image assets; keep reusable parser work here until
  it is stable enough to feed `rag_pdfs`.
- `rag_langchain/` — frozen historical exploration material. Do not extend it;
  its useful code was already merged into `rag_pdfs` with imports rewritten.
- `configs/` — pydantic-settings config; secrets live in `configs/.env` (gitignored).

## Read Order

1. `rag_pdfs/strata.md` — launch charter (goals, non-goals, success criteria)
2. `agent_rag_growth_roadmap.md` — product line + agent-learning roadmap
3. `rag_pdfs/notes.md` — design notes (blog conclusions + experiment design)
4. `rag_pdfs/plan.md` — engineering blueprint and module map
5. `parsers/plan.md` — read when working on parser or Markdown export tools
6. `rag_pdfs/logs.md` — append-only run history
7. Code entry points: `rag_pdfs/ingest_img.py`, `rag_pdfs/query_img.py`, `rag_pdfs/eval_img.py`; parser entry point: `python -m parsers.redox_opendataloaderpdf`

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
- `strata.md` — shim pointing to `rag_pdfs/strata.md`
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
