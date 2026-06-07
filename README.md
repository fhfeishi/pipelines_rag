# pipelines-rag

面向技术 PDF 的 image-index RAG 实验项目。核心策略是：

```text
Vision at ingestion, text at retrieval.
```

也就是在索引阶段用 VLM 把图片读成可检索的 caption 文本；查询阶段只检索 text chunk 和 image_caption chunk，不再把大量原图塞给多模态模型。

## 安装

```bash
uv sync
```

运行 PDF 解析需要 Java 11+。生成图片 caption 需要配置 vision provider API key；默认使用 DashScope：

```bash
export DASHSCOPE_API_KEY=...
export QWEN3_EMBEDDING_06B_PATH=/path/to/qwen3-embedding-0.6b
```

也可以在 `configs/.env` 中配置 `dashscope_api_key`、`deepseek_api_key`、`qwen3_embedding_06b_path`。

## 使用

单 PDF ingest：

```bash
uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf \
  --out tmp/outs/foo \
  --vision-provider dashscope \
  --build-chroma
```

仅重建 chunk / Chroma，不调用 VLM：

```bash
uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf \
  --out tmp/outs/foo \
  --skip-parse \
  --dry-run \
  --build-chroma
```

批量处理 `tmp/raws/*.pdf`：

```bash
uv run -m rag_langchain.ingest_img \
  --batch \
  --vision-provider dashscope \
  --build-chroma
```

查询索引：

```bash
uv run -m rag_langchain.query_img \
  --index tmp/outs/foo \
  --strategy separate \
  --show-evidence \
  "How do automated tests act as backpressure?"
```

批量跑实验矩阵，只评估检索 evidence，不调用答案 LLM：

```bash
uv run -m rag_langchain.eval_img \
  --questions rag_langchain/eval_questions.sample.jsonl \
  --retrieve-only \
  --strategies text_only,inline,separate \
  --retrievals hybrid,vector \
  --alphas 0.3,0.5,0.7 \
  --limit 4
```

默认输出：

```text
tmp/eval_runs/image_rag_eval.jsonl
tmp/eval_runs/image_rag_eval.jsonl.summary.json
```

可选策略：

- `--strategy text_only`：只查文本 baseline。
- `--strategy inline`：caption 插回正文后检索。
- `--strategy separate`：文本 chunk 与独立 image_caption chunk 混合检索，默认策略。
- `--retrieval hybrid|vector`：默认 hybrid，使用 BM25 + 向量融合。
- `--alpha 0.5`：hybrid 中向量分数权重。

## 输出

每个 PDF 默认输出到 `tmp/outs/{pdf_stem}/`：

```text
image_captions.jsonl
skipped_images.jsonl
text_only_chunks.jsonl
inline_caption_chunks.jsonl
separate_caption_chunks.jsonl
separate_mixed_chunks.jsonl
summary.json
chroma/
```

`summary.json` 记录 chunk 数、caption 数、跳过图片数和 caption API 统计。

## 字幕策略

这里的“字幕”指图片 caption。caption 不是普通看图说话，而是面向 RAG 的证据转写：

- caption 在 ingest 阶段生成并缓存到 `image_captions.jsonl`。
- prompt 会注入 section title、图片前后文本，帮助 VLM 把图片落到具体工作流。
- 表格、矩阵、架构图、UI 截图应尽量转录标签、数值、路径和方向关系。
- 小图、logo、装饰图会先经 `pdf_filter.py` 的 L1/L2 规则过滤。
- `--no-caption-context` 用于消融实验：不提供上下文生成 caption。
- `--resume` 或 `--dry-run` 会优先复用已有真实 caption，避免覆盖缓存。

## 验证

常用检查命令：

```bash
uv run -m rag_langchain.ingest_img --help
uv run -m rag_langchain.query_img --help
uv run -m rag_langchain.query_img --index tmp/outs/foo --strategy separate --show-evidence "question"
```

验证重点：

- `image_captions.jsonl` 中没有非预期的 `DRY RUN CAPTION`。
- `skipped_images.jsonl` 没有误跳过关键表格、图示或截图。
- `separate` 策略能独立召回 `image_caption` evidence。
- `inline` / `separate` / `text_only` 使用同一 chunk size、embedding model 和 top-k 做对照。
- `eval_img.py --retrieve-only` 能批量输出独立 image evidence 数量、inline image mention 数量、context 字符数、gold image recall@k。

## 当前边界

- PDF parser 仍是实验路径，集中在 `rag_langchain/pdf_*`。
- 当前 query 默认使用 hybrid BM25 + vector；cross-encoder reranker 暂缓。
- 评估框架已有 `eval_img.py` 骨架；系统化标注问题集仍需扩充。
- 中文/中英混排 BM25 分词仍是简单 whitespace + lower 策略。
- `core/indexing_img.py` 仍是占位文件，主入口以 `rag_langchain.ingest_img` 为准。
