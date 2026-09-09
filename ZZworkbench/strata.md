> RAG Solutions pipeline  
>
> `RAG = Retrieval + ContextConstruction + Generation`

---

```html
External Knowledge
      ↓
Index / Organize
      ↓
Retrieve
      ↓
Select / Rerank / Reason
      ↓
Context Construction
      ↓
LLM Generation
```

---

`RAG Retrieval Taxonomy`

1. Lexical Retrievalh
  - tokenizer / analyzer
  - inverted index
  - BM25
  - exact-term / sparse retrieval
2. Dense / Vector Retrieval
  - embedding models
    - text
    - vision
    - multimodal
    - document / section / chunk granularity
  - vector store
  - ANN index
    - HNSW
    - IVF
    - Flat
  - similarity
    - cosine
    - dot product
    - L2
  - key limitation:
  semantic similarity ≠ relevance
3. Structured Retrieval
  - metadata filtering
  - SQL / structured query
  - B-tree / Hash indexes
4. Graph Retrieval
  - entity / relation graph
  - graph traversal
  - multi-hop retrieval
  - community-level retrieval
  - GraphRAG / LightRAG
  - often combined with vector retrieval
5. Reasoning / Structure-aware Retrieval
  - hierarchical document index
  - tree search
  - LLM-guided retrieval
  - PageIndex

`Orthogonal System Architectures`

- Hybrid RAG
lexical + dense + metadata + reranking
- Graph RAG
graph representation + graph/vector/text retrieval
- Multimodal RAG
text / image / audio / video retrieval
- Agentic RAG
LLM plans and iteratively selects retrieval tools

`Knowledge Representation / Knowledge Packaging`

- LLM Wiki
- Google Open Knowledge Format (OKF)
- Markdown / structured knowledge bundles

---

> 视觉信息（如图像）嵌入到 RAG_pipeline
> step1:图像到知识库文本，保存、映射，图像跟与之关联的知识库文本建立`链接`。存，只发生一次。
> step2:答案文本到图文输出展示，检索、排版。

> embedding问题，

> 初步探索`RAG pipeline`
> step1: 解析文档中的图文信息，补全上下文，重构出每一条语义清晰完整的信息，比如：表格数据需要理解表头、重构出完整的上下文。图片可能需要准换成文本处理，image_caption包含 image, image-role, image_description
> 评估问题应标注图片角色image-role：

- `none`：不需要图片。
- `illustrative`：图片辅助理解。
- `load-bearing`：答案本身依赖图片、表格或流程图。
- `others`: 图片？有很多样的形式，普通的jpg，excel中联合单元格填充颜色构建的图表？（这种解析出来绝对是零散的无意义的颜色块、文本短语）

step2：`langchain_core.documents.Document`类封装