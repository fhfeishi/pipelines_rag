# Ingest Image 运行日志

> 对照 [`plan.md`](plan.md) 记录真实 PDF 运行结果、分析与下一步。  
> 目录约定：`tmp/raws/{name}.pdf` → `tmp/outs/{name}/`

**最后更新**：2026-06-07（LangChain eval_img 批量实验骨架）

---

## 1. 数据集与最终状态

| PDF | 页数 | Caption | 跳过 | DRY RUN | Chroma | 状态 |
|-----|------|---------|------|---------|--------|------|
| HowToFixAISlopUsingHermesX | 14 | 8 | 1 | **0** | ✅ 50/66/58 | 真实 VLM 完成 |
| BackpressureIsAllYouNeed | 17 | 12 | 1 | **0** | ✅ 52/74/64 | 真实 VLM 完成 |

Chroma 三列 = `text_only` / `inline_caption` / `separate_mixed` 文档数。

---

## 2. Chunk 统计（最终）

| PDF | text_only | inline | separate_caption | separate_mixed | inline/text |
|-----|-----------|--------|------------------|----------------|-------------|
| HermesX | 50 | 66 | 8 | 58 | 1.32× |
| Backpressure | 52 | 74 | 12 | 64 | 1.42× |

- inline chunk 数高于 text-only，但单 chunk 平均长度接近（~485–505 字符）
- Backpressure 真实 caption 后 inline=74（DRY RUN 占位时曾虚高到 90/91）

---

## 3. Caption 质量观察

### HermesX（8 图）
- `section_title` 仅 1/8 有值（页眉 `(1) X`）；其余为流程图，parser 无 heading
- 图示 caption 较好：EVAL LOOP、threshold 0.7、Hermes 节点等可检索
- `image_path` 仍为绝对路径（早期 ingest）；新 ingest 会写相对路径

### Backpressure（12 图）
- `section_title` 12/12（跨页 heading 查找生效）
- `image_path` 均为相对路径 `images/imageFileN.png`
- VLM：12 次 API，约 59s（~4.9s/图），DashScope `qwen-vl-max`
- 流程图 caption 示例：Developer → TypeScript → Automated Tests → Reviewer 反馈链

### 过滤
- 两 PDF 均跳过 page 1 的 `imageFile1.png`（bbox 面积 ≈144，`bbox_area<4000`）

---

## 4. 本次修复：dry-run 不再覆盖真实 caption

**问题**：`--dry-run --build-chroma` 曾重写 `image_captions.jsonl` 为 DRY RUN 占位符。

**修复**（`ingest_img.py`）：
1. `load_caption_cache` + `is_placeholder_caption` — dry-run/resume 时复用真实 caption
2. `captions_for_persist()` — 写入 jsonl 前用磁盘真实 caption 覆盖占位
3. `write_outputs(..., dry_run=, caption_cache_path=)` — 已接线（2026-06-06 补全）
4. dry-run 重建 Chroma 时**保留** `summary.json` 里已有的真实 `caption_stats`

**验证**（2026-06-06）：
```bash
uv run -m rag_langchain.ingest_img tmp/raws/BackpressureIsAllYouNeed.pdf \
  --out tmp/outs/BackpressureIsAllYouNeed --skip-parse --dry-run
# → 12× [cached]，image_captions.jsonl 中 DRY RUN = 0
```

---

## 5. Query 三策略对比（2026-06-07）

命令：`uv run -m rag_langchain.query_img --index tmp/outs/{PDF} --strategy {text_only|inline|separate} [--show-evidence] "question"`

参数：`fetch_k=12`，`top_k=6`；LLM = DeepSeek v4-flash。

### 5.1 对比表

| PDF | 问题 | 策略 | 召回 evidence | 答案质量（简评） |
|-----|------|------|---------------|------------------|
| HermesX | What is the eval loop? | text_only | text×6 | ✅ 定义准确（repeatable test、closes loop）；缺图示隐喻 |
| HermesX | What is the eval loop? | inline | text×6 | ✅ 同上 + 三处运行场景；仍无独立 image chunk |
| HermesX | What is the eval loop? | **separate** | text×5, **image×1** (imgcap-0077 p14) | ✅ 最佳：正文定义 + eye 隐喻图 + "eval loop is the system" |
| HermesX | How does Hermes fix AI slop? | text_only | text×6 | ✅ gate/cron/memory/thumbs-down 步骤完整 |
| HermesX | How does Hermes fix AI slop? | inline | text×6（内嵌 caption） | ✅ **最佳**：threshold 0.7、JUDGE/GATE 流程图细节（imgcap-0020/0070/0055 经 inline 召回） |
| HermesX | How does Hermes fix AI slop? | separate | text×5, **image×1** (imgcap-0055 p10) | ✅ 好；仅 1 张 Hermes Eval Loop 图进 top-6，不如 inline 覆盖全 |
| Backpressure | What is backpressure in software development? | text_only | text×6 | ✅ 下游信号、测试拒绝机制；无流程图 |
| Backpressure | What is backpressure in software development? | inline | text×6（内嵌 caption） | ✅ 正文 + 内嵌 page 2 图 caption 引用 |
| Backpressure | What is backpressure in software development? | separate | text×3, **image×3** (0013/0033/0028) | ✅ 最丰富；3 张流程图 caption 占 half context |
| Backpressure | How do automated tests act as backpressure? | text_only | text×6 | ✅ PR 须全绿、测试套件即 backpressure |
| Backpressure | How do automated tests act as backpressure? | inline | text×6（内嵌 caption） | ✅ 同上 + page 2 迭代反馈图 caption |
| Backpressure | How do automated tests act as backpressure? | **separate** | text×4, **image×2** (0013 p2, 0033 p5) | ✅ 最佳：正文 + 两张 workflow 图互证 |

> **image_caption chunk 仅 `separate` 策略以独立 `[Image evidence]` 召回**；`inline` 将 caption 嵌入 text chunk（检索计数仍为 text×6，但 LLM 可引用 `imgcap-*`）；`text_only` 永不召回 caption。

### 5.2 `--show-evidence` 样例

**HermesX / separate / eval loop** — top-6 含 imgcap-0077（page 14 eye 隐喻，nearby text 含 "eval loop is the system"）。

**Backpressure / separate / automated tests** — top-6 含 imgcap-0013（Developer→Tests→Reviewer 迭代图）+ imgcap-0033（Agent backpressure 循环图）。

### 5.3 小结

| 维度 | 观察 |
|------|------|
| 纯定义题（eval loop、backpressure 定义） | text_only/inline 已够用；separate 多图示语境 |
| 流程/组件题（Hermes fix slop、tests as backpressure） | **inline 常优于 separate**（caption 与正文同 chunk，不被 top-k 挤掉） |
| separate 风险 | 无 reranker 时 image chunk 可占 3/6 context（backpressure 定义题），挤压正文 |
| 下一步 | ~~接 cross-encoder reranker~~ → **已用 hybrid BM25+vector**（§6）；后续可再加 reranker |

---

## 6. Hybrid 检索（2026-06-07）

模块：`rag_langchain/hybrid_retrieve.py`；CLI 默认 `--retrieval hybrid`。

**融合公式**（min-max 归一化后）：

```text
final = alpha * norm_vector + (1 - alpha) * norm_bm25
```

- BM25：`rank_bm25.BM25Okapi`，语料来自 `{strategy}_chunks.jsonl`（或 Chroma fallback）
- 向量：Qwen3 embedding 余弦相似度（`normalize_embeddings=True`）
- 分词：空白切分 + lower（v1，中英文混排可接受）

### 6.1 Backpressure / separate / 快测

问题：`How do automated tests act as backpressure?`；`fetch_k=12`，`top_k=6`，`alpha=0.5`。

| 排名 | vector-only | hybrid |
|------|-------------|--------|
| 1 | text-0027 | **imgcap-0013** (image) |
| 2 | imgcap-0013 | text-0035 |
| 3 | text-0026 | imgcap-0021 (image) |
| 4 | text-0042 | text-0027 |
| 5 | imgcap-0033 | imgcap-0033 |
| 6 | text-0031 | text-0026 |

**差异**：hybrid 将 workflow 图 imgcap-0013 升至 #1（BM25 命中 "automated tests" 关键词）；新增 imgcap-0021、text-0035；挤出 text-0042、text-0031。

**alpha 调参提示**：
- `alpha→1`：趋近纯向量（当前 vector-only 行为）
- `alpha→0`：趋近纯 BM25（关键词匹配强，caption 中术语多时 image chunk 易上浮）
- separate 策略 image 占比过高时可试 `alpha=0.6~0.7` 抬高向量权重

### 6.2 命令

```bash
# 默认 hybrid
uv run -m rag_langchain.query_img \
  --index tmp/outs/BackpressureIsAllYouNeed \
  --strategy separate --show-evidence \
  "How do automated tests act as backpressure?"

# 对照：纯向量
uv run -m rag_langchain.query_img \
  --index tmp/outs/BackpressureIsAllYouNeed \
  --strategy separate --retrieval vector --show-evidence \
  "How do automated tests act as backpressure?"

# 调 alpha（更偏 BM25）
uv run -m rag_langchain.query_img \
  --index tmp/outs/BackpressureIsAllYouNeed \
  --alpha 0.3 --show-evidence "question"
```

---

## 7. 常用命令

```bash
# 单 PDF 完整 ingest
uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf \
  --out tmp/outs/foo --vision-provider dashscope --build-chroma

# 仅重建 Chroma（不调用 VLM、不覆盖 caption）
uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf \
  --out tmp/outs/foo --skip-parse --dry-run --build-chroma

# 批量
uv run -m rag_langchain.ingest_img --batch --vision-provider dashscope --build-chroma
```

---

## 8. 对照 plan.md 进度

| Phase | 状态 | 备注 |
|-------|------|------|
| 0 解析 + section_title | ✅ | 跨页 heading 已做 |
| 1 图片过滤 L1/L2 | ✅ | 面积、长宽比、稀疏上下文 |
| 2 VLM caption + resume | ✅ | dry-run 保护已加 |
| 3 inline / separate chunk | ✅ | `caption_chunks.py` |
| 4 Chroma 四 collection | ✅ | 两 PDF 均已建 |
| 5 query_img | 🔄 | hybrid BM25+vector 已实现；cross-encoder reranker 暂缓 |
| 6 A/B/C 实验 | ⬜ | 缺标注问题集 + 定量指标 |

---

## 9. 下一步（plan.md Milestone 2–3）

1. **Hybrid alpha 调参 + 定量对比**
   - 在标注问题集上对比 `alpha=0.3/0.5/0.7` 与 vector-only 的 caption recall@k
   - 记录 fusion promotion rate（caption 因 BM25/vector 融合升降名次）
2. **Cross-encoder reranker（可选增强）**
   - hybrid 已替代 reranker 作为 Phase 5 默认路径；若仍不足再接入
3. **标注问题集**：text-only / image-helpful / image-required 三类，支撑 Phase 6 定量 A/B/C
4. **HermesX 相对路径**：可选 `--skip-parse` 重跑 caption 统一 metadata
5. **无 heading 页的 section 启发式**：用页内首段短标题补锚点

---

## 10. 产物路径

```
tmp/outs/{PDF名}/
├── image_captions.jsonl      # 每张图 caption（勿用 dry-run 无 cache 时覆盖）
├── skipped_images.jsonl
├── text_only_chunks.jsonl
├── inline_caption_chunks.jsonl
├── separate_caption_chunks.jsonl
├── separate_mixed_chunks.jsonl
├── summary.json
└── chroma/{text_only,inline_caption,separate_caption,separate_mixed}/
```

---

## 11. 文档职责收敛（2026-06-07）

目标：把项目文档从“探索材料混放”收敛为 agent 可维护的固定契约，方便继续推进 image-index RAG。

### 11.1 调整内容

| 文件 | 职责 |
|------|------|
| `README.md` | 重写为简洁用户手册：安装、使用、输出、字幕/caption 策略、验证、当前边界 |
| `AGENTS.md` | 新增 canonical agent guide：开发规则、验证命令、文档维护契约 |
| `strata.md` | 新增项目 launch charter：存在原因、pipeline、成功标准、文档契约 |
| `CLAUDE.md` | 新增 3 行 compatibility shim，只指向 `AGENTS.md` |
| `plan.md` | 修正 CLI 入口描述：ingest=`rag_langchain.ingest_img`，query=`rag_langchain.query_img`；同步已完成状态 |
| `logs.md` | 追加本次文档职责收敛记录 |

### 11.2 当前 canonical 入口

```bash
# ingest
uv run -m rag_langchain.ingest_img tmp/raws/foo.pdf --out tmp/outs/foo --build-chroma

# query
uv run -m rag_langchain.query_img --index tmp/outs/foo --strategy separate --show-evidence "question"
```

### 11.3 后续维护规则

- 用户怎么跑项目：更新 `README.md`。
- agent 怎么开发和验证：更新 `AGENTS.md`。
- 项目为什么存在、成功标准、文档契约：更新 `strata.md`。
- image-index RAG 技术路线变化：更新 `plan.md`。
- 真实运行、修复、验证、文档收敛过程：追加 `logs.md`。

---

## 12. LangChain 批量实验骨架（2026-06-07）

目标：把手工 query 对比升级为可重复的实验矩阵，继续完善 image-index RAG 的 A/B/C 对照。

### 12.1 新增能力

| 文件 | 内容 |
|------|------|
| `rag_langchain/eval_img.py` | 新增批量实验 CLI，读取 JSONL 问题集，遍历 strategy / retrieval / alpha |
| `rag_langchain/eval_questions.sample.jsonl` | 新增最小样例问题集，固定 `gold_image_ids`、`gold_chunk_ids`、`image_role` schema |
| `rag_langchain/query_img.py` | `load_vectorstore(..., embeddings=...)` 支持复用 embedding 实例，避免批量实验重复加载模型 |
| `rag_langchain/hybrid_retrieve.py` | 新增 `HybridRetriever`，优先复用 Chroma 已存 document embeddings，alpha sweep 只需 embed query |

### 12.2 评测输出

`eval_img.py` 每个 run 输出一行 JSONL，包含：

- `strategy` / `retrieval` / `alpha`
- retrieved evidence 的 `chunk_id`、`chunk_type`、`image_id`、`page`
- `image_evidence_count`
- `inline_image_mention_count`
- `image_reference_count`
- `image_evidence_ratio`
- `context_chars`
- `gold_image_hit`
- `gold_chunk_hit`
- 可选 LLM answer（默认可用 `--retrieve-only` 只跑检索）

summary JSON 按 `(strategy, retrieval, alpha)` 聚合：

- runs
- avg image evidence count
- avg context chars
- gold image recall@k
- gold chunk recall@k

### 12.3 命令

```bash
uv run -m rag_langchain.eval_img \
  --questions rag_langchain/eval_questions.sample.jsonl \
  --retrieve-only \
  --strategies text_only,inline,separate \
  --retrievals hybrid,vector \
  --alphas 0.3,0.5,0.7
```

默认产物：

```text
tmp/eval_runs/image_rag_eval.jsonl
tmp/eval_runs/image_rag_eval.jsonl.summary.json
```

### 12.4 后续

- 扩充 `eval_questions.sample.jsonl` 为正式标注集。
- 补 `gold_chunk_ids`，让 text evidence recall@k 也可用。
- 增加 answer correctness / groundedness 的人工或 LLM judge 字段。
- 用 summary 比较 `alpha=0.3/0.5/0.7` 对 image caption recall 与 context 膨胀的影响。

### 12.5 验证结果

```bash
uv run python -m compileall rag_langchain/hybrid_retrieve.py rag_langchain/query_img.py rag_langchain/eval_img.py
uv run -m rag_langchain.eval_img --help
uv run -m rag_langchain.eval_img \
  --questions rag_langchain/eval_questions.sample.jsonl \
  --retrieve-only \
  --strategies text_only,inline,separate \
  --retrievals hybrid,vector \
  --alphas 0.3,0.5,0.7 \
  --out tmp/eval_runs/sample_eval_matrix.jsonl
```

结果：4 个问题 × 3 策略 × 4 retrieval/alpha 组合 = **48 rows**，完成于约 22s。

关键观察（sample 很小，只作 smoke/方向判断）：

| variant | gold image recall@k | avg image refs | 备注 |
|---------|---------------------|----------------|------|
| text_only / hybrid | 0.00 | 0.00 | baseline 不含图片 |
| inline / hybrid alpha=0.7 | 0.75 | 2.00 | inline caption 可通过 text chunk 间接命中 |
| separate / hybrid alpha=0.7 | 0.75 | 1.75 | 独立 image_caption 命中较好 |
| separate / vector | 1.00 | 1.75 | sample 上纯向量召回最高，需扩大标注集确认 |

性能修复：最初 hybrid smoke 因重新 embed 全语料，单 case 约 185s；改为读取 Chroma stored embeddings 后，单 case 检索约 2.2s。

---

## 13. 2026 私有知识问答 Pipelines 文档（2026-06-08）

目标：把“2026 年私有知识问答不应默认盲建向量库”的架构判断整理为独立 Markdown，并核验关键断言。

### 13.1 新增文档

| 文件 | 内容 |
|------|------|
| `private_knowledge_qa_2026.md` | 2026 私有知识问答 Pipelines 选型指南：真实性审查、决策模型、六类 pipeline、评估体系、对本项目 image-index RAG 的落地建议 |

### 13.2 关键结论

- RAG 没有退场，但已经从默认答案变成需要按场景选择的工程组件。
- 小型稳定知识库可优先考虑长上下文 + Prompt Caching。
- 大规模、频繁更新、强权限审计的非结构化文档仍适合生产级 RAG。
- 代码库问答更适合 agentic retrieval + grep/read/index 混合搜索。
- 结构化业务数据应优先走 SQL/API，而不是强行向量化。
- 技术 PDF 场景应继续围绕 `text_only` / `inline` / `separate image_caption` 做定量评估。

### 13.3 验证

通过：

```bash
wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m rag_langchain.ingest_img --help

wsl -d Ubuntu-22.04 --cd /home/baheas/wslcodespace/pipelines_rag \
  /home/baheas/.local/bin/uv run -m rag_langchain.query_img --help
```

备注：直接在 PowerShell UNC 路径下运行 `uv run ...` 时，`uv` 创建 `.venv` 的 `lib64` 链接清理失败；改用 WSL 原生路径后通过。
