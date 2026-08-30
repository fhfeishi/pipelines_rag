# pipelines-rag

PDF image RAG pipeline. Indexes the images inside technical-documentation PDFs
(screenshots, tables, architecture diagrams) as retrievable text evidence, so a
text-only RAG query path can answer questions whose answers live in figures.

Approach: **vision at ingestion, text at retrieval** — a VLM captions each
kept image once during ingest; retrieval is hybrid BM25 + vector over text and
caption chunks. No multimodal calls at query time.

## Layout

| Path | Role |
|------|------|
| `rag_pdfs/` | Canonical package: ingest / query / eval CLIs |
| `rag_langchain/` | Frozen historical exploration (do not extend) |
| `configs/` | Settings; secrets in `configs/.env` (gitignored) |

## Quickstart

Requires Python 3.12+, Java 11+ (for opendataloader-pdf), and API keys in
`configs/.env` (`dashscope_api_key`, `deepseek_api_key`).

```bash
uv sync

# PDF -> parse -> filter -> caption -> chunk -> Chroma
uv run -m rag_pdfs.ingest_img path/to/doc.pdf \
  --out tmp/outs/doc --build-chroma

# Ask a question against the index
uv run -m rag_pdfs.query_img --index tmp/outs/doc \
  "Where is the retention setting?"

# Batch experiments (strategy x retrieval mode x alpha)
uv run -m rag_pdfs.eval_img --questions rag_pdfs/eval_questions.sample.jsonl
```

The legacy CLI reads API keys and `QWEN3_EMBEDDING_06B_PATH` from
`configs/.env`. For a role-based deployment, copy and edit
`configs/models.example.toml`, then pass `--model-config configs/models.toml`
or set `RAG_MODEL_CONFIG`. New Chroma builds write `index_manifest.json`; query
and eval reject embedding fingerprint mismatches. Use
`--strict-index-manifest` to also reject pre-manifest legacy indexes.

See `rag_pdfs/plan.md` for the module map and `rag_pdfs/strata.md` for the
project charter. Agent instructions live in `AGENTS.md`.
