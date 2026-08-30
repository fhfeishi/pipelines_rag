

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
---


# 如何提升你的第一性原理思维能力

第一性原理思维是创新领域最常被谈论的能力，也是最常被误解的能力。下面说说它真正的样子。

原文作者：Phil McKinney  

## 什么是第一性原理思维？

第一性原理思维，是把一个问题拆解到最基本的真相，再从真正站得住脚的事实出发，重新构建解决方案的做法。

不是从行业惯例出发。  
不是从上一次奏效的方法出发。  
而是从眼前这个问题真正成立的事实出发。

它的对立面是类比推理：沿用以前有效的方法，模仿竞争对手的做法，或者遵循品类中的默认期待。类比更快，而且通常是对的。但当过去成立的事情不再成立，而所有人都没有察觉时，它会失败得很惨。

## 为什么假设总是没有被检验？

2005 年，在帕洛阿尔托 20 号楼的走廊里，惠普 CEO Mark Hurd 拦住我，追问我关于惠普研发投入的问题。他关注的指标是研发投入占营收的比例。他希望惠普的比例看起来更像宏碁。我提出了反对意见。我认为，我们应该拿自己和苹果比，而不是和宏碁比。

Mark 毫不犹豫地说：“我们不是苹果，也永远不会是苹果。”

那一刻让我停住的，并不是分歧本身，而是那种确定性。房间里没有人质疑：研发投入占营收比例这个指标，是否真的衡量了我们以为它在衡量的东西。这个指标已经用了几十年。每个竞争对手都在用。每个分析师都在追踪。它感觉像是不可动摇的基石。

但它并不是。它只是一个继承下来的限制，后来逐渐硬化成了一条规则。研发投入占营收比例告诉你的，是会计分类上的信息。它无法告诉你这些投入产生了什么成果，无法告诉你团队是否在攻克正确的问题，也无法告诉你创新产出是在增长还是在萎缩。

隐藏在这个指标背后的假设，从未被检验过。没有人问过：把商业模式完全不同的公司放在一起比较研发比例，是否真的有意义。

这个未经检验的假设所造成的代价，并没有在下一个季度显现出来。它是在接下来的十年里逐渐显现的。惠普的创新管线悄无声息地枯竭，我们连续三年获得的《Fast Company》“最具创新力公司”认可，也随之消失。

一个继承下来的指标，被一整屋经验丰富的人当作事实接受，并据此做出影响一代人的决策。

这就是衍生式思维真正的代价。不是一个糟糕的季度，而是一个十年。

那个房间里的人并不粗心。他们都很有经验。而经验恰恰会让继承下来的假设看起来像事实。那个指标感觉像事实。它其实是一个没人记得自己曾经做过的选择。这正是一个第一性原理问题本该捕捉到的东西。但没人问。

## 三项核心能力

这三项能力是按顺序运行的，而且每一项都依赖前一项。

第一项是**剥离假设**：找出问题被表述时已经内置进去的继承性假设。  
第二项是**检验剩下的内容，并在此基础上重新构建**：把经得起检验的部分留下来，从真正成立的事实出发搭建解决方案。  
第三项是**判断什么时候该使用第一性原理**：确认这个过程是否值得启动。

如果跳过前面的步骤，后面的技能就站不住。按顺序做，它们会相互叠加，效果会越来越强。

## 剥离假设

在你能够从第一性原理出发思考之前，你必须先知道自己到底在处理什么。大多数问题在被提出时，已经带着某种假设。你的第一项工作，就是把它们找出来。

剥离假设的步骤：

1. **原封不动地写下问题。**  
   不要急着优化表述。使用对方原本的措辞。

2. **划出每一个暗示约束的词。**  
   比如“必须”“不能”“总是”“绝不”“唯一的方法是”。每一个都可能是候选假设。

3. **逐一追问每个约束：这是物理上真实成立，还是继承而来的？**  
   物理真相不会因为你的决定而改变。继承性约束则是某个人过去的决定，后来硬化成了规则。

4. **把继承性约束先放到一边，重新表述剩下的问题。**  
   这才是真正的问题。它通常比你一开始面对的问题更小，也更容易解决。

5. **把剩下的内容当作你的设计约束。**  
   这些才是真正的边界。把这份清单带入头脑风暴，用它来检验每个想法，而不是用那些你已经划掉的假设来检验。

如果你认真做，这一步只需要 20 分钟。大多数团队会完全跳过它，然后花几个月时间去优化一个错误问题的解决方案。

## 检验剩下的内容，并在此基础上重新构建

不是每个约束都是假设。有些事情确实真实存在：物理规律、单位经济模型、大规模下的人类行为。目标不是假装这些约束不存在，而是精确判断你面对的到底是哪一种现实。

检验并重新构建的步骤：

1. **拿每一个留下来的约束继续追问。**  
   问自己：这是真的因为它在物理上无法改变，还是因为改变它会很昂贵、不熟悉、让人不舒服？昂贵和不熟悉，并不等于不可能。

2. **区分硬性限制和软性限制。**  
   硬性限制是真正成立的内容：无论你如何重新定义问题，它们都成立。软性限制是可以协商的。把它们清楚地标出来。大多数团队从不做这个区分，而是把每个限制都当成花岗岩一样坚硬。

3. **用简单直白的语言写下你的硬性限制。**  
   写下来。每个硬性限制用一句话表达。这些才是你的解决方案必须尊重的真实边界。

4. **从剩下的真实内容向前推理。**  
   不要从行业现状出发，再向后倒推来为它辩护。现在要问的是：这些硬性限制支持什么样的解决方案？

最后一步，正是意外解决方案诞生的地方。

当你从惯例向后推理时，得到的往往只是现有答案的改良版本。它的形状很熟悉，因为你一开始就是从熟悉的答案出发的。

但当你从硬性限制向前推理时，你可能会到达一个品类原本没有预料到的位置，因为你没有被现有答案的形状锚定。这样构建出来的解决方案，起初往往会让人觉得奇怪。人们会质疑它们。

这种不适感，通常说明你发现的是某种真实存在的东西，而不是某种继承下来的东西。这就是从真正成立的事实出发推理所带来的结果，而不是从所有人默认接受的假设出发。

## 什么时候使用第一性原理？

在启动这个过程之前，先问四个问题。只要有一个答案是“是”，就值得使用。

1. 这个决策原本所适用的环境，是否已经发生了重大变化？
2. 桌面上的所有方案，是否感觉都只是同一种东西的变体？
3. 目前的方法，是继承而来的，而不是主动选择的吗？
4. 如果这里存在一个错误假设，它带来的代价是否会超过一个下午的排查和修正成本？

如果四个问题的答案都是否，那么过去的经验就是合适的工具。用它就好。

花 20 分钟剥离假设，成本很低。真正昂贵的是跳过这一步。

## 假设反转练习

做这个练习，你需要一个搭档。先让对方看这段视频。他们需要先知道什么是继承性假设，才能发现你的假设。等你们都准备好后，可以去 innovation.tools 获取免费的“第一性原理思维检查清单”，或者在说明里找到链接。它会给你们一个共同的参考点，再开始练习。

具体做法如下：

1. **每个人带来一个真实问题。**  
   这个问题必须是当前正在发生、有实际利害关系的事情。不要拿思想实验来练习。这个问题应该是你一直在脑子里反复思考，却没有得到满意答案的事情。

2. **处理搭档的问题，而不是自己的问题。**  
   你要找出他们在表述问题时内置进去的假设。他们也会对你的问题做同样的事。这个方法之所以有效，是因为你比他们更容易看清他们继承下来的限制。你不像他们那样深陷在问题内部。

3. **每个人列出自己能在对方问题中找到的所有假设。**  
   写下来。先不要争论，也不要评判。只要尽可能多地把假设浮现出来。数量很重要。显而易见的假设很容易找到，要继续往更深处推。

4. **把每个假设反转过来。**  
   如果假设是“这需要大量预算”，反转就是“如果它不需要预算，会出现什么可能性？”  
   如果假设是“客户不会接受另一种形式”，反转就是“如果客户愿意接受，我们会构建什么？”  
   不要问这个反转是否现实。要问它打开了什么可能性。

5. **讨论这些反转揭示了什么。**  
   不是每个被反转的假设都会导向有用的方向。但通常会有一个反转暴露出一个限制：它原本并没有你以为的那么固定。那就是值得继续追下去的地方。

反转练习的重点很简单：有些假设经得起推敲，有些经不起。你不试，就不知道是哪一种。

## 长期游戏

每一次你运行这个过程，并发现某个原本看似成立的东西其实站不住脚，你识别假设的速度就会变快。你也会更准确地判断什么时候该使用它。

这就是能力在实践中提升的样子：不是戏剧性的灵光一现，而是一种经过训练的能力——在房间里的假设找到你之前，你先找到它。

最让你付出代价的假设，不是那个你还没想到的假设。  
而是那个你多年前就停止质疑的假设。

找一个搭档。本周就做一次“假设反转”练习。第一性原理思维，正是从这里开始变成一种真正的能力。


---------
# 



# 