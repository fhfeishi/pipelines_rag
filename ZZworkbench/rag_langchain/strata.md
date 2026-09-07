> 基于框架 `langchain` 的`Agent Solution`开发记录


----
- RAG
  - 1. vector based retrieve
      - hierarchical-embedding, (query --> document, chunk, embedding, index-vector_db-persist_dense/BM25 -> fuse)
      - multi-embedding
  - 2. non-vector based retrieve
      - PageIndex: VLM + document  inference
  - 3. graph based retireve