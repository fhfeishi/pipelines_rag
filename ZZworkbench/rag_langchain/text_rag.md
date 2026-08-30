# 纯文本知识库检索基线

这条管线只负责检索，不负责生成：

```text
UTF-8 TXT
  -> langchain_core.documents.Document
  -> 中文段落优先分块
  -> embedding + Chroma
  -> metadata 工程路由
  -> dense + 中文 n-gram BM25
  -> weighted RRF
  -> top-k Document
```

## 为什么直接返回 Document

不需要再继承或包装一层 `Document`。TXT 读取和分块应该是普通函数，返回
LangChain 的标准类型；只有带分数和排名的查询结果使用轻量
`RetrievalHit(document, rank, score, distance)`。

父文档的 `id` 由相对来源路径和文件内容哈希确定。chunk 的 `id` 由父文档
ID、起始位置、chunk 内容和分块配置确定。因此同一输入和配置会得到稳定 ID，
源文件或分块参数变化则会得到新 ID。

关键 metadata 均为 Chroma 可接受的标量：

- `source`、`source_name`、`title`、`corpus_version`
- `source_sha256`、`document_id`、`chunk_id`
- `chunk_index`、`start_index`、`end_index`
- `mime_type`、`encoding`、`loader`

未来的图像 caption 只需遵守同一 `Document` 契约，并增加 `modality=image`、
`image_path`、`page`、`bbox` 等 metadata；检索层无需认识具体 parser。

## 当前知识库结论

`knowledge/project_progress/texts` 有 v1-v4 四个版本，每版 8 个 TXT，共
32 个文件、199641 个字符，全部可按严格 UTF-8 解码。v2/v4 之间有两对完全
重复文件，其余是同一工程的近重复版本。

默认只索引 `v4`。全量历史版本必须显式传 `--version all`，否则近重复 chunk
会挤占 top-k。v4 当前得到 8 个父 `Document`、63 个 chunk；分块长度为
240-868 字符。

## 使用

在仓库根目录运行：

```bash
# 只读审计，并展示 Document/chunk 示例
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py audit

# 使用本机已下载的 GTE-large 建立 v4 Chroma；相同清单会直接复用
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py index

# 默认走 metadata 路由 + dense/BM25 RRF
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py query \
  "珠海黄金输变电工程的主体结构封顶计划什么时候完成？"

# 保留纯 dense 对照
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py query \
  "珠海黄金输变电工程的主体结构封顶计划什么时候完成？" \
  --strategy dense

# 当前 8 条人工标注问题
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py eval
```

索引默认写入 `outputs/text_rag/v4`。`index_manifest.json` 固定记录语料哈希、
chunk 参数、embedding 契约和索引指纹。已有索引与请求配置不一致时会拒绝
继续；确认目标目录后显式使用 `--rebuild`，只重置指定 Chroma collection。

## Embedding 实例化

默认使用已经下载到本地仓库的模型：

```bash
--embedding-backend local \
--embedding-source local \
--embedding-model /mnt/e/local_models/embedding/iic--nlp_gte_sentence-embedding_chinese-large
```

Hugging Face 缓存模式：

```bash
--embedding-backend local \
--embedding-source huggingface \
--embedding-model BAAI/bge-m3 \
--cache-root /path/to/model-cache
```

ModelScope 缓存模式把 `--embedding-source` 改为 `modelscope`。缓存目录通过
`HF_HOME`、`HUGGINGFACE_HUB_CACHE`、`MODELSCOPE_CACHE` 设置；模型缓存不是
可执行文件搜索路径，所以不会修改系统 `PATH`。

OpenAI 兼容 embeddings API：

```bash
export EMBEDDING_API_KEY=...
.venv/bin/python ZZworkbench/rag_langchain/deterministic-pipe.py index \
  --embedding-backend openai \
  --embedding-model text-embedding-3-small \
  --embedding-base-url https://example.com/v1 \
  --api-key-env EMBEDDING_API_KEY
```

API key 不进入配置对象、索引清单或日志。索引和查询必须使用同一 embedding
指纹；否则 CLI 会拒绝查询，防止用不同向量空间造成静默错误。

## 检索策略与当前指标

该语料包含大量工程名、任务名、编号和日期。纯 dense 会把“主体封顶”召回到
语义相近但工程错误的片段，因此 hybrid 先用 `title + source_name` 做工程级
metadata 路由，再在候选工程中融合：

- dense cosine：语义改写和近义表达；
- 中文 2/3 字符 n-gram BM25：工程名、任务名、编号和日期；
- weighted RRF：当前 v4 小评测集采用 dense weight 0.40。

在当前 8 条标注问题、top-4 上：

| 策略 | source_hit@4 | answer-term_hit@4 |
|---|---:|---:|
| dense baseline | 0.50 | 0.00 |
| metadata-routed hybrid | 1.00 | 1.00 |

这只是用于防回归的小样本，不是泛化结论。下一步应扩充容易混淆的负例、没有
工程名的查询、跨文档问题和无答案问题，并分别报告路由、召回和重排指标。

## LangSmith

`ScoredChromaRetriever` 和 `HybridChromaRetriever` 都继承 LangChain
`BaseRetriever`，查询统一调用 `.invoke()`，因此可直接进入 LangChain callback
生命周期。配置下列环境变量即可记录 retriever trace：

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=...
export LANGSMITH_PROJECT=pipelines-rag-text-retrieval
```

追踪应重点观察 query、路由是否触发、dense/BM25 排名、最终 top-k、延迟和评测
标签。当前阶段没有 prompt template、chat model、记忆或上下文压缩；这些属于
后续生成层，不应混进检索基线。
