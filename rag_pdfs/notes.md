

---
# Ingest Image in RAG

> **实验性 PDF ingest**：实现细节与进度见 [`plan.md`](plan.md)、运行记录见 [`logs.md`](logs.md)。  
> 模块：`ingest_img.py`（CLI）+ `pdf_parser` / `pdf_filter` / `pdf_layout` + `caption_chunks.py`

- 关键结论

技术文档中的图片值得进入RAG，但更合适的方式不是在查询时把图片发给多模态模型，而是在ingest/indexing阶段把图片读一次，转换成文本caption，再让caption参与普通文本检索。
> Vison at ingestion, text at retrieval. 

```text
PDF / docs
-> extract images 
-> generate image captions with sourrounding text
-> store captions as text 
-> retrieve text chunks + caption chunks at querry time 
```

在这背后的原因有三个：
- 查询时多模态太贵：原文测试中，raw images 让 GPT 单次查询成本增加 27%，让 Claude 增加 51%。
- 查询时图片放不下：典型问题可能召回 20-30 张图片，长尾超过 130 张，很快撞到 payload 限制。
- 图像向量检索不适合技术文档：CLIP-style embeddings 容易丢掉表格、图示、截图标注中的细节。


图片在技术文档中主要有两类价值：
- illustrative：图片解释文本已经说过的东西，让用户更容易操作。
- load-bearing：图片本身承载答案，比如表格、矩阵、架构图、接线图。

## 两种 caption 存储方式

### 方案 A：Inline caption

做法：把图片 caption 插回原文档中图片所在位置，类似替换或扩展 image alt text，然后再按普通文本切 chunk。

示例：

```text
Open Settings > Integrations.

[Image: Screenshot showing the Cloud Sync toggle in Settings > Integrations > Cloud Sync.]

Click Save.
```

索引后可能得到：

```json
{
  "chunk_type": "text",
  "text": "Open Settings > Integrations. [Image: Screenshot showing the Cloud Sync toggle...] Click Save.",
  "source": "manual.pdf",
  "page": 12
}
```

优点：

- 实现简单。
- caption 和附近正文天然绑定。
- 适合快速做 baseline。

问题：

- caption 会让文本 chunk 变胖。
- 只要正文 chunk 被召回，caption 就被动进入上下文，即使问题不需要图片。
- 图片密集文档中，很多 chunk 会被 caption 膨胀。
- 如果一个 chunk 周围有多张图，图片证据和正文证据会混在一起，不容易追踪哪张图真正支撑答案。

原文结果：在一个图片密集项目上，inline caption 让 GPT per-query cost 增加 19%。

### 方案 B：Separate caption chunks

做法：原文 text chunks 保持不变。每张有用图片生成一个独立 `image_caption` chunk，并带上图片路径、页码、bbox、附近上下文等 metadata。

示例：

```json
{
  "id": "imgcap-012-001",
  "chunk_type": "image_caption",
  "text": "Table of audit log retention by plan: Free keeps logs for 7 days, Pro for 90 days, Enterprise for 365 days.",
  "source": "manual.pdf",
  "page": 12,
  "image_path": "./images/page-12-image-01.png",
  "bbox": [100, 240, 820, 480],
  "nearby_text_before": "Audit logs are available depending on your plan.",
  "nearby_text_after": "Export logs from the Security page."
}
```

查询时：

```text
query
-> retrieve text chunks + image_caption chunks
-> rerank mixed candidates
-> include only relevant caption chunks in final context
-> answer can cite image_path/page/bbox
```

优点：

- caption 只有相关时才进入上下文，query token 更可控。
- 图片可以作为独立证据被召回。
- 更适合 load-bearing images，比如表格、矩阵、图示。
- 更容易追踪答案引用了哪张图。
- text chunk 和 image_caption chunk 可以分别调参和评估。

问题：

- 实现复杂度更高。
- 需要维护图片 chunk 与原始图片位置的映射。
- retriever/reranker 要能混合处理 text chunks 和 image_caption chunks。
- caption 质量变得很关键，尤其是表格值、图示标签、UI 路径是否被准确转录。

原文结果：separate caption chunks 在同一个图片密集项目上只让 GPT per-query cost 增加 6%；对 Claude，甚至略低于 text-only。re-ranker 在 51% 的查询中把 caption chunk 推进 top 15，同时整体排序稳定（Spearman ρ = 0.905）。

## 实现关键点

### 1. 图片抽取

用 `opendataloader-pdf` 解析 PDF，至少保留：

```json
{
  "doc_id": "manual",
  "page": 12,
  "element_type": "image",
  "image_path": "./images/page-12-image-01.png",
  "bbox": [x1, y1, x2, y2],
  "before_text": "...",
  "after_text": "..."
}
```

同时保留普通文本块，用于构建 text-only baseline 和提供 image caption 的 surrounding context。

### 2. 图片过滤

不要对所有图片生成 caption。先过滤（实现：`pdf_filter.py`）：

- logo、头像、装饰图、社交预览图。
- 过小图片（bbox 面积）。
- 小图 + 极端长宽比（横幅/分隔线）。
- 页眉/页脚极小图。
- unsupported format / 文件缺失。

进一步可以用 VLM 或轻量分类器判断图片类型（L3，未实现）：

```text
useful: screenshot / table / diagram / chart / schematic
noise: logo / avatar / decorative / social card
uncertain: requires surrounding text
```

原文提醒：单靠像素无法解决所有分类问题。比如同一张倒计时截图，可能是装饰图，也可能是教程步骤。因此图片过滤最好也使用 surrounding text。

### 3. Caption 生成

caption 不是普通看图说话，而是面向 RAG 检索和 answer grounding 的文字转写。

输入给 VLM：

- image
- before_text
- after_text
- page / section title

推荐 prompt 方向：

```text
Describe this image for a RAG index over technical documentation.
Use the surrounding text to ground the caption in the specific product, workflow, or step.
If the image contains UI labels, table values, diagram labels, numbers, warnings, configuration paths, or steps, transcribe them.
Do not invent details.
Return only the caption.
```

caption 需要区分图片类型：

- 对 illustrative screenshot：描述用户能操作的路径、按钮、位置。
- 对 load-bearing table/matrix：尽量转录表格值、行列关系、条件。
- 对 diagram/schematic：转录节点、边、标签、方向、关键约束。

原文的重要经验是：上下文比模型大小更重要。没有上下文时，caption 容易泛化成 “a web page with a file upload form”；有上下文时，caption 才能落到具体产品、工作流和步骤上。

## 对比试验设计

### 实验目标

验证在技术文档 RAG 中，图片 caption 的不同存储方式对答案质量、检索效果、成本和引用能力的影响。

核心问题：

> 图片 caption 应该被视为正文的一部分，还是被视为一种独立证据？

### 实验组

| 组别 | 名称 | Ingest 方式 | Query 方式 | 目的 |
| --- | --- | --- | --- | --- |
| A | Text-only baseline | 只抽取文本，不处理图片 | 只检索文本 chunk | 看没有图片时的基础能力 |
| B | Inline caption | ingest 时生成 caption，插回图片位置，再切 chunk | 检索普通文本 chunk | 测 caption 绑定正文的效果 |
| C | Separate caption chunks | ingest 时生成 caption，每张图独立成 image_caption chunk | 混合检索 text chunk + image_caption chunk | 测原文推荐方案 |
| D | Separate without context | 同 C，但生成 caption 时不提供 before/after text | 混合检索 | 验证 surrounding context 的价值 |

可选对照组：

| 组别 | 名称 | 说明 |
| --- | --- | --- |
| E | Query-time multimodal | 查询时把召回文本引用的图片一起发给多模态模型。这个组主要用于证明它贵且难以规模化，不一定作为主线方案。 |

后续实验组（2026-07-10 设计，排在 A/B/C/D 定量结论之后；背景见根 `strata.md` §3.2c）：

| 组别 | 名称 | Ingest/知识形态 | Query 方式 | 目的 |
| --- | --- | --- | --- | --- |
| F | Concept retrieval | Layer 1.5 蒸馏：文档 → OKF 式概念文件（frontmatter + evidence 链接指回文档层） | 概念文件级检索（或概念优先、chunk 兜底） | 测"策展知识层"对高频问题的精度增益；依赖蒸馏立项 |
| G | Agentic markdown navigation | 无向量索引；直接用 StaticParsePackage 的 document.md + index.md | agent 用 grep / 标题树（section_path）导航定位证据 | 测"规范 markdown + agent 主动检索"能否替代 embedding 检索，以及在什么语料规模/问题类型下失效 |

F/G 的评估与 A–D 同一标注集、同一指标（gold image/chunk recall、答案质量、token、latency），
额外记录 G 组的导航步数与读取字符数（agent 检索的成本形态不同于 top-k）。
预期：G 在小语料、结构清晰的文档上有竞争力；语料变大或问题落在长尾细节时，
hybrid 检索（C 组形态）仍是必要的降级路径——这正是 Layer 2–3 作为"检索工具箱"不可删的原因。

最小实验可以只做：

```text
A. Text-only baseline
B. Inline caption
C. Separate caption chunks
```

### 数据集

选择 5-20 份真实技术文档 PDF，最好覆盖不同图片密度和图片类型：

- UI 操作手册：多截图，主要测试 illustrative images。
- datasheet / spec：多表格、矩阵、图，主要测试 load-bearing images。
- 架构/部署文档：多架构图、流程图。
- 图片很少的 API 文档：作为接近 text-only 的对照。

问题集可以分三类：

| 问题类型 | 示例 | 预期图片价值 |
| --- | --- | --- |
| Text-only answerable | 某参数默认值是什么？ | 图片不重要 |
| Image helpful | 如何找到某个设置入口？ | 截图提升可操作性 |
| Image required | Enterprise plan 的 audit log retention 是多少天？ | 表格/矩阵可能是答案来源 |

每个问题需要标注：

- gold answer
- gold evidence text chunk
- gold evidence image id，如果有
- image role：none / illustrative / load-bearing

### 实验流程

1. 用同一批 PDF 生成三套或四套索引。
2. 每套索引用同一个 embedding model、同一个 chunk size、同一个 retriever、同一个 reranker。
3. 对同一批问题分别运行 RAG。
4. 固定生成模型和 prompt，避免生成侧变量太多。
5. 记录召回证据、最终上下文、答案、引用图片、token、延迟和成本。
6. 用人工评估或 LLM judge 做两两比较。

为了公平，除 caption 存储方式以外，其它变量尽量固定：

- same PDF parser
- same image filtering rule
- same caption model
- same caption prompt
- same embedding model
- same top_k / rerank_top_k
- same answer generation prompt

### 指标

质量指标：

| 指标 | 解释 |
| --- | --- |
| Answer correctness | 答案是否正确 |
| Groundedness | 答案是否能被召回证据支持 |
| LLM judge preference | 两个实验组答案的偏好比较 |
| Image usefulness | 被引用图片是否真的帮助用户理解或回答问题 |
| Image placement accuracy | 图片是否被放在正确答案附近 |

检索指标：

| 指标 | 解释 |
| --- | --- |
| Evidence recall@k | gold evidence 是否进入 top-k |
| Image caption recall@k | 需要图片的问题中，正确 image_caption 是否进入 top-k |
| Rerank promotion rate | image_caption 被 reranker 提升进 top-k 的比例 |
| Ranking stability | 加入 caption 后，普通文本 chunk 排名是否大幅扰动 |

成本与性能指标：

| 指标 | 解释 |
| --- | --- |
| Indexing cost | 图片 caption 的一次性生成成本 |
| Per-query input tokens | 最终上下文 token 数 |
| Per-query API cost | 查询时模型调用成本 |
| Time to first token | 首 token 延迟 |
| Context image-caption ratio | 最终上下文中 caption token 占比 |

### 预期结果

Text-only baseline：

- 对纯文本问题表现稳定，成本最低。
- 对 image helpful / image required 问题容易给出模糊答案，或者漏掉图中的值。

Inline caption：

- 答案质量会高于 text-only。
- 对说明性截图有帮助。
- 但会让相关正文 chunk 变胖，图片密集文档中 per-query token 成本上升明显。
- 图片 caption 可能在不相关问题中被动进入上下文。

Separate caption chunks：

- 答案质量应接近或优于 inline。
- 对 load-bearing images 更有优势，因为图片 caption 作为独立证据被召回。
- per-query token 更可控，因为 caption 只有相关时才进入上下文。
- 更容易做图片引用和证据追踪。

Separate without context：

- 预期弱于正常 separate。
- 主要用于验证 caption 生成时 surrounding text 的价值。
- 如果差异明显，说明 caption 质量问题不能只靠更大模型解决，必须设计好上下文注入。

Query-time multimodal：

- 可能质量高，但成本和 payload 限制会很快暴露。
- 如果图片数量需要强行截断，反而可能漏掉关键图。
- 更适合作为上限对照，而不是生产方案。

## 最终要回答的问题

这个实验最终不是为了证明“图片有用”这么宽泛的结论，而是为了回答一个更工程化的问题：

> 在技术文档 RAG 中，图片 caption 应该 inline 到正文里，还是作为独立 image_caption chunk 进入索引？

基于原文经验，我的预期是：

- 如果只想快速验证，inline caption 是简单 baseline。
- 如果面向生产和规模化，separate caption chunks 更合理。
- caption 生成时必须提供 surrounding text。
- 对表格、矩阵、图示等 load-bearing images，caption 应该尽量转录结构和值，而不是只写视觉描述。

一句话总结：

> 把图片读成文本只是第一步；更关键的是把读出来的文本当作独立证据，而不是无条件塞回正文。
