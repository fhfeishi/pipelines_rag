# Agent Guide

This is the canonical guide for agents working on `pipelines-rag`. `CLAUDE.md` is only a compatibility shim and must point back here.

## Mission

Develop and evaluate an image-index RAG pipeline for technical PDFs:

```text
PDF -> layout parse -> image filter -> VLM caption -> text/image chunks -> Chroma -> hybrid retrieval -> answer with evidence
```

The project exists to test whether image captions should be indexed inline with text or as separate `image_caption` evidence chunks.

## Read Order

1. `strata.md` for the launch charter and document contract.
2. `README.md` for user-facing setup and commands.
3. `rag_langchain/plan.md` for the current engineering blueprint.
4. `rag_langchain/logs.md` for real run history and decisions.
5. `rag_langchain/notes.md` only as background research and experiment notes.
6. Code entry points: `rag_langchain/ingest_img.py`, `rag_langchain/query_img.py`, `rag_langchain/hybrid_retrieve.py`.
7. Experiment entry point: `rag_langchain/eval_img.py`.

## Development Rules

- Keep the CLI entry points canonical:
  - ingest: `uv run -m rag_langchain.ingest_img`
  - query: `uv run -m rag_langchain.query_img`
- Keep PDF parser-specific code under `rag_langchain/pdf_*`.
- Keep reusable chunk and retrieval logic outside `pdf_*`.
- Prefer conservative image filtering: extra captions are cheaper than dropping load-bearing evidence.
- Preserve caption cache safety. `--dry-run` must not overwrite real captions with placeholders.
- Store image paths in queryable metadata and keep `image_id`, `page`, `chunk_type`, `chunk_id`, and `source_pdf` consistent.
- Do not introduce a second agent workflow in `CLAUDE.md` or elsewhere.

## Validation Rules

For doc-only changes:

```bash
uv run -m rag_langchain.ingest_img --help
uv run -m rag_langchain.query_img --help
```

For ingest changes:

```bash
uv run -m rag_langchain.ingest_img tmp/raws/BackpressureIsAllYouNeed.pdf \
  --out tmp/outs/BackpressureIsAllYouNeed \
  --skip-parse \
  --dry-run
```

For index/query changes:

```bash
uv run -m rag_langchain.ingest_img tmp/raws/BackpressureIsAllYouNeed.pdf \
  --out tmp/outs/BackpressureIsAllYouNeed \
  --skip-parse \
  --dry-run \
  --build-chroma

uv run -m rag_langchain.query_img \
  --index tmp/outs/BackpressureIsAllYouNeed \
  --strategy separate \
  --show-evidence \
  "How do automated tests act as backpressure?"
```

For evaluation changes:

```bash
uv run -m rag_langchain.eval_img \
  --questions rag_langchain/eval_questions.sample.jsonl \
  --retrieve-only \
  --strategies text_only,inline,separate \
  --retrievals hybrid,vector \
  --alphas 0.3,0.5,0.7 \
  --limit 4
```

If local API keys or embedding models are unavailable, still run import/help checks and record what could not be verified.

## Documentation Contract

- `README.md`: concise user manual only.
- `AGENTS.md`: canonical development and validation guide for agents.
- `strata.md`: launch charter: why the project exists, pipeline, success criteria, document contract.
- `CLAUDE.md`: three-line compatibility shim pointing to `AGENTS.md`.
- `rag_langchain/plan.md`: technical plan for image-index RAG.
- `rag_langchain/logs.md`: append-only record of runs, fixes, and documentation decisions.
- `rag_langchain/notes.md`: research notes and longer background material; do not treat it as the active plan.

When you change behavior, update `plan.md` if the blueprint changes and append `logs.md` with the actual work and validation. When you only clarify user-facing usage, update `README.md` and append `logs.md`.

## Current Priority

Continue the image-index RAG track by tightening evaluation and retrieval:

- build a small labeled question set with `none / illustrative / load-bearing` image roles;
- compare `text_only`, `inline`, and `separate` under the same parameters with `eval_img.py`;
- tune hybrid `alpha` and measure image caption recall@k from JSONL outputs;
- add reranker only if hybrid fusion is insufficient.
