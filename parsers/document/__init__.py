"""Layer 1 共享 parse 核心：layout 元素模型、opendataloader 调用、包读写。

依赖方向约束：本包只依赖 stdlib / pydantic / opendataloader-pdf，
**禁止** import `rag_pdfs`（Layer 2–3）。
"""
