"""Versioned prompts used by the deterministic RAG workflow.

Prompts belong to the application/workflow layer, not to model classes. Local
source control remains canonical; a LangSmith Hub identifier can be recorded as
deployment metadata later without making the application depend on the network
to boot.
"""

from langchain_core.prompts import ChatPromptTemplate

ANSWER_PROMPT_VERSION = "rag-answer-v1"
QUERY_REWRITE_PROMPT_VERSION = "rag-query-rewrite-v1"
HISTORY_SUMMARY_PROMPT_VERSION = "rag-history-summary-v1"

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You answer questions using only the provided evidence from a technical document. "
                "Distinguish text evidence from image caption evidence. "
                "When an image caption supports the answer, cite image_id and page. "
                "If evidence is insufficient, say so. Do not invent details."
            ),
        ),
        (
            "human",
            "Question:\n{question}\n\nEvidence:\n{context}\n\nAnswer:",
        ),
    ]
)

QUERY_REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "Rewrite the latest user question as one standalone retrieval query. "
                "Use conversation context only to resolve references. Preserve product names, "
                "versions, identifiers, paths, numbers, and technical terms. "
                "Return only the query."
            ),
        ),
        (
            "human",
            (
                "Conversation summary:\n{history_summary}\n\n"
                "Recent messages:\n{recent_messages}\n\n"
                "Latest question:\n{question}"
            ),
        ),
    ]
)

HISTORY_SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "Compress older conversation history for a technical RAG assistant. "
                "Keep unresolved questions, user constraints, decisions, exact identifiers, "
                "document names, versions, paths, and corrections. Do not treat the summary as "
                "retrieved evidence and do not invent facts."
            ),
        ),
        (
            "human",
            "Existing summary:\n{existing_summary}\n\nMessages to summarize:\n{messages}",
        ),
    ]
)

PROMPT_VERSIONS = {
    "answer": ANSWER_PROMPT_VERSION,
    "query_rewrite": QUERY_REWRITE_PROMPT_VERSION,
    "history_summary": HISTORY_SUMMARY_PROMPT_VERSION,
}

__all__ = [
    "ANSWER_PROMPT",
    "ANSWER_PROMPT_VERSION",
    "HISTORY_SUMMARY_PROMPT",
    "HISTORY_SUMMARY_PROMPT_VERSION",
    "PROMPT_VERSIONS",
    "QUERY_REWRITE_PROMPT",
    "QUERY_REWRITE_PROMPT_VERSION",
]
