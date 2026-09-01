"""Reliable lookup contract for the small project-progress knowledge base.

The v4 corpus is not open-domain prose. It contains eight semi-structured
schedule documents whose records repeat task names across projects. This
module therefore treats dense retrieval as candidate recall, while project
routing, exact task-record matching, field checks, and abstention determine
whether a fact is safe to answer.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from langchain_core.documents import Document

from ZZworkbench.rag_langchain.text_retrieval import RetrievalHit


EvidenceStatus = Literal[
    "exact",
    "ambiguous",
    "not_found",
    "insufficient",
    "conflict",
]
RequestedField = Literal["start_date", "end_date", "duration"]

PAGE_PATTERN = re.compile(r"以下内容来自PDF第(?P<page>\d+)页。")
TASK_ID_PATTERN = re.compile(r"^标识号(?P<task_id>\d+)是")
PARENT_ID_PATTERN = re.compile(r"[（(]标识号(?P<parent_id>\d+)[）)]")
QUOTED_PATTERN = re.compile(r"“(?P<value>[^”]+)”")
DURATION_PATTERN = re.compile(r"工期(?P<value>[^。]+)")
START_PATTERN = re.compile(r"计划开始(?P<value>\d{4}年\d{1,2}月\d{1,2}日)")
END_PATTERN = re.compile(r"计划完成(?P<value>\d{4}年\d{1,2}月\d{1,2}日)")


# These aliases are intentionally small and corpus-specific. They are easier
# to audit than an LLM-generated project router and cover the names that occur
# in the current v4 files and evaluation questions.
SOURCE_ALIAS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "110kV黄金输变电工程三级进度计划.txt",
        (
            "黄金",
            "黄金输变电工程",
            "珠海黄金输变电工程",
            "110千伏黄金输变电工程",
            "110kV黄金输变电工程",
        ),
    ),
    (
        "110千伏节点计划-重点关注.txt",
        (
            "110千伏节点计划",
            "110千伏输变电工程节点计划",
            "输变电工程节点计划",
            "节点计划",
        ),
    ),
    (
        "三级进度计划-土建.txt",
        (
            "禾益",
            "禾益输变电工程",
            "禾益输变电工程变电站土建",
            "珠海110千伏禾益输变电工程",
        ),
    ),
    (
        "三虎输变电工程三级进度计划土建部分.txt",
        (
            "三虎",
            "三虎输变电工程",
            "三虎土建",
            "三虎输变电工程土建部分",
            "珠海110千伏三虎输变电工程土建部分",
        ),
    ),
    (
        "三虎输变电工程三级进度计划电气部分.txt",
        (
            "三虎",
            "三虎输变电工程",
            "三虎电气",
            "三虎输变电工程电气部分",
            "珠海110千伏三虎输变电工程电气部分",
        ),
    ),
    (
        "南溪三级进度.txt",
        (
            "南溪",
            "南溪输变电工程",
            "南溪旅游输变电工程",
            "南溪（旅游）输变电工程",
            "珠海110千伏南溪（旅游）输变电工程",
        ),
    ),
    (
        "珠海110kV江湾输变电工程总体进度计划横道图.txt",
        (
            "江湾",
            "江湾输变电工程",
            "江湾总体计划",
            "江湾输变电工程总体计划",
            "江湾110千伏输变电工程总体计划",
            "珠海江湾110千伏输变电工程总体计划",
        ),
    ),
    (
        "珠海110千伏江湾输变电工程施工进度计划（202.txt",
        (
            "江湾",
            "江湾输变电工程",
            "江湾施工进度计划",
            "江湾输变电工程施工进度计划",
            "珠海110千伏江湾输变电工程施工进度计划",
        ),
    ),
)


def normalize_lookup_text(value: str) -> str:
    """Normalize names for deterministic matching without losing Chinese text."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"(?P<voltage>\d+)\s*k\s*v", r"\g<voltage>千伏", normalized)
    normalized = normalized.replace("签订", "签定")
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


@dataclass(frozen=True, slots=True)
class QueryIntent:
    original_query: str
    normalized_query: str
    project_hint: str | None
    task_hint: str | None
    requested_fields: tuple[RequestedField, ...]
    query_type: str
    project_candidates: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    source: str
    source_name: str
    document_id: str
    chunk_id: str | None
    project_title: str
    page: int | None
    task_id: str
    task_name: str
    parent_task: str | None
    parent_task_id: str | None
    relation: str
    is_parent: bool
    is_child: bool
    duration: str | None
    start_date: str | None
    end_date: str | None
    raw_text: str

    @property
    def record_id(self) -> str:
        return f"{self.document_id}:{self.task_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, **asdict(self)}


@dataclass(frozen=True, slots=True)
class ReliableQueryResult:
    intent: QueryIntent
    status: EvidenceStatus
    records: tuple[ScheduleRecord, ...]
    answer: str
    retrieval_calls: int
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "status": self.status,
            "records": [record.to_dict() for record in self.records],
            "answer": self.answer,
            "retrieval_calls": self.retrieval_calls,
            "diagnostics": dict(self.diagnostics),
        }


RetrieverFn = Callable[[str], Sequence[RetrievalHit]]


def _requested_fields(query: str) -> tuple[RequestedField, ...]:
    normalized = normalize_lookup_text(query)
    asks_duration = "多久" in normalized or "工期" in normalized
    asks_start = (
        "开始" in normalized
        or "起止" in normalized
        or "在什么时间" in normalized
        or "什么时间段" in normalized
        or "多久" in normalized
    )
    asks_end = (
        "完成" in normalized
        or "结束" in normalized
        or "起止" in normalized
        or "在什么时间" in normalized
        or "什么时间段" in normalized
        or "多久" in normalized
    )
    fields: list[RequestedField] = []
    if asks_start:
        fields.append("start_date")
    if asks_end:
        fields.append("end_date")
    if asks_duration:
        fields.append("duration")
    return tuple(fields) or ("start_date", "end_date")


def _heuristic_task_hint(query: str) -> str | None:
    """Extract the task phrase only after exact corpus-name matching fails."""

    segment = unicodedata.normalize("NFKC", query).strip().split("的")[-1]
    suffixes = (
        r"计划开始、完成和工期分别是什么.*$",
        r"计划的开始和完成日期是什么.*$",
        r"计划的开始和完成时间是什么.*$",
        r"计划起止时间是什么.*$",
        r"计划什么时候完成.*$",
        r"计划什么时候开始.*$",
        r"什么时候完成.*$",
        r"什么时候开始.*$",
        r"计划多久.*$",
        r"在什么时间.*$",
        r"的时间是什么.*$",
    )
    for suffix in suffixes:
        segment = re.sub(suffix, "", segment).strip("？?。 ，,")
    segment = re.sub(r"计划$", "", segment).strip()
    return segment or None


def _aliases_for_source(source_name: str, title: str) -> tuple[str, ...]:
    aliases = [title, source_name.rsplit(".", 1)[0]]
    for expected_source, configured in SOURCE_ALIAS_RULES:
        if source_name == expected_source:
            aliases.extend(configured)
            break
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


class ProjectProgressKnowledgeBase:
    """Parsed v4 schedule records plus deterministic evidence gating."""

    def __init__(
        self,
        documents: Sequence[Document],
        chunks: Sequence[Document],
    ) -> None:
        self.documents = tuple(documents)
        self.chunks = tuple(chunks)
        self._document_by_source = {
            str(document.metadata["source_name"]): document for document in documents
        }
        self._aliases = {
            source_name: _aliases_for_source(
                source_name,
                str(document.metadata.get("title", source_name)),
            )
            for source_name, document in self._document_by_source.items()
        }
        self.records = tuple(self._parse_records())

    def _parse_records(self) -> list[ScheduleRecord]:
        records: list[ScheduleRecord] = []
        chunks_by_document: dict[str, list[Document]] = {}
        for chunk in self.chunks:
            document_id = str(chunk.metadata.get("document_id", ""))
            chunks_by_document.setdefault(document_id, []).append(chunk)

        for document in self.documents:
            source = str(document.metadata["source"])
            source_name = str(document.metadata["source_name"])
            document_id = str(document.metadata["document_id"])
            title = str(document.metadata.get("title", source_name))
            page: int | None = None
            for paragraph in re.split(r"\n\s*\n", document.page_content):
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                if page_match := PAGE_PATTERN.fullmatch(paragraph):
                    page = int(page_match.group("page"))
                    continue
                task_match = TASK_ID_PATTERN.match(paragraph)
                if task_match is None:
                    continue

                task_id = task_match.group("task_id")
                header = paragraph.split("。", 1)[0]
                quoted = [match.group("value") for match in QUOTED_PATTERN.finditer(header)]
                if not quoted:
                    continue
                task_name = quoted[-1]
                parent_task = quoted[-2] if len(quoted) > 1 else None
                parent_match = PARENT_ID_PATTERN.search(header)
                parent_task_id = parent_match.group("parent_id") if parent_match else None
                is_child = "子任务" in header
                # "是父任务 X 的子任务 Y" describes X as the parent. Y is
                # itself a parent only when the record says "同时是父任务".
                is_parent = "同时是父任务" in header or (
                    header.startswith(f"标识号{task_id}是父任务") and not is_child
                )
                if "顶层独立任务" in header:
                    relation = "top_level"
                elif is_child and is_parent:
                    relation = "child_parent"
                elif is_child:
                    relation = "child"
                elif is_parent:
                    relation = "parent"
                else:
                    relation = "task"

                duration_match = DURATION_PATTERN.search(paragraph)
                start_match = START_PATTERN.search(paragraph)
                end_match = END_PATTERN.search(paragraph)
                chunk_id = self._locate_chunk_id(
                    chunks_by_document.get(document_id, []),
                    paragraph,
                    task_id,
                    task_name,
                )
                records.append(
                    ScheduleRecord(
                        source=source,
                        source_name=source_name,
                        document_id=document_id,
                        chunk_id=chunk_id,
                        project_title=title,
                        page=page,
                        task_id=task_id,
                        task_name=task_name,
                        parent_task=parent_task,
                        parent_task_id=parent_task_id,
                        relation=relation,
                        is_parent=is_parent,
                        is_child=is_child,
                        duration=(duration_match.group("value").strip() if duration_match else None),
                        start_date=(start_match.group("value") if start_match else None),
                        end_date=(end_match.group("value") if end_match else None),
                        raw_text=paragraph,
                    )
                )
        return records

    @staticmethod
    def _locate_chunk_id(
        chunks: Sequence[Document],
        paragraph: str,
        task_id: str,
        task_name: str,
    ) -> str | None:
        ordered = sorted(chunks, key=lambda chunk: int(chunk.metadata.get("chunk_index", 0)))
        for chunk in ordered:
            if paragraph in chunk.page_content:
                return str(chunk.id or chunk.metadata.get("chunk_id"))
        marker = f"标识号{task_id}是"
        for chunk in ordered:
            if marker in chunk.page_content and task_name in chunk.page_content:
                return str(chunk.id or chunk.metadata.get("chunk_id"))
        return None

    def audit(self) -> dict[str, Any]:
        task_names: dict[str, set[str]] = {}
        for record in self.records:
            task_names.setdefault(normalize_lookup_text(record.task_name), set()).add(
                record.source_name
            )
        repeated = {
            name: sorted(sources)
            for name, sources in task_names.items()
            if len(sources) > 1
        }
        return {
            "documents": len(self.documents),
            "chunks": len(self.chunks),
            "records": len(self.records),
            "records_without_chunk": sum(not record.chunk_id for record in self.records),
            "records_without_start": sum(not record.start_date for record in self.records),
            "records_without_end": sum(not record.end_date for record in self.records),
            "records_without_duration": sum(not record.duration for record in self.records),
            "repeated_task_names_across_sources": repeated,
        }

    def resolve_project_sources(self, query: str) -> tuple[str, ...]:
        normalized_query = normalize_lookup_text(query)
        scores: list[tuple[str, int]] = []
        for source_name, aliases in self._aliases.items():
            matched_lengths = [
                len(normalized_alias)
                for alias in aliases
                if (normalized_alias := normalize_lookup_text(alias))
                and normalized_alias in normalized_query
            ]
            scores.append((source_name, max(matched_lengths, default=0)))
        best = max((score for _, score in scores), default=0)
        if best == 0:
            return ()
        return tuple(sorted(source for source, score in scores if score == best))

    def parse_query(self, query: str) -> QueryIntent:
        if not query.strip():
            raise ValueError("query cannot be empty")
        normalized_query = normalize_lookup_text(query)
        project_candidates = self.resolve_project_sources(query)
        scoped_records = [
            record
            for record in self.records
            if not project_candidates or record.source_name in project_candidates
        ]
        exact_names = {
            record.task_name
            for record in scoped_records
            if normalize_lookup_text(record.task_name) in normalized_query
        }
        task_hint = max(exact_names, key=lambda value: len(normalize_lookup_text(value))) if exact_names else None

        diagnostics: list[str] = []
        if project_candidates:
            diagnostics.append(f"project route matched {len(project_candidates)} source(s)")
        else:
            diagnostics.append("no explicit project route")

        if task_hint is None and len(project_candidates) == 1 and "总体计划" in normalized_query:
            roots = [
                record
                for record in scoped_records
                if record.task_id == "1" and record.parent_task_id is None
            ]
            if len(roots) == 1:
                task_hint = roots[0].task_name
                diagnostics.append("mapped overall-plan query to root task")

        if task_hint is None:
            task_hint = _heuristic_task_hint(query)
            if task_hint:
                diagnostics.append("used conservative task-phrase fallback")

        matching_records = [
            record
            for record in scoped_records
            if task_hint
            and normalize_lookup_text(record.task_name) == normalize_lookup_text(task_hint)
        ]
        query_type = "single_lookup"
        if not task_hint:
            query_type = "unsupported"
        elif len({record.source_name for record in matching_records}) > 1:
            query_type = "ambiguous"

        project_hint = None
        if project_candidates:
            project_hint = " | ".join(
                str(self._document_by_source[source].metadata.get("title", source))
                for source in project_candidates
            )
        return QueryIntent(
            original_query=query,
            normalized_query=normalized_query,
            project_hint=project_hint,
            task_hint=task_hint,
            requested_fields=_requested_fields(query),
            query_type=query_type,
            project_candidates=project_candidates,
            diagnostics=tuple(diagnostics),
        )

    def lookup(
        self,
        query: str,
        *,
        retriever: RetrieverFn | None = None,
    ) -> ReliableQueryResult:
        return self.lookup_intent(self.parse_query(query), retriever=retriever)

    def lookup_fields(
        self,
        *,
        project: str,
        task: str,
        fields: Sequence[RequestedField],
        retriever: RetrieverFn | None = None,
    ) -> ReliableQueryResult:
        requested = tuple(dict.fromkeys(fields))
        allowed = {"start_date", "end_date", "duration"}
        if not requested or any(field not in allowed for field in requested):
            raise ValueError("fields must contain start_date, end_date, or duration")
        project_candidates = self.resolve_project_sources(project)
        intent = QueryIntent(
            original_query=f"{project} / {task}",
            normalized_query=normalize_lookup_text(f"{project}{task}"),
            project_hint=project,
            task_hint=task,
            requested_fields=requested,  # type: ignore[arg-type]
            query_type="single_lookup",
            project_candidates=project_candidates,
            diagnostics=("structured tool input",),
        )
        return self.lookup_intent(intent, retriever=retriever)

    def lookup_intent(
        self,
        intent: QueryIntent,
        *,
        retriever: RetrieverFn | None = None,
    ) -> ReliableQueryResult:
        hits = list(retriever(intent.normalized_query)) if retriever else []
        retrieval_calls = 1 if retriever else 0
        hit_by_chunk = {
            str(hit.document.id or hit.document.metadata.get("chunk_id")): hit
            for hit in hits
        }
        diagnostics: dict[str, Any] = {
            "project_candidates": list(intent.project_candidates),
            "retrieved_chunk_ids": list(hit_by_chunk),
            "retrieved_sources": [
                str(hit.document.metadata.get("source_name", "")) for hit in hits
            ],
        }

        if not intent.task_hint:
            return self._result(
                intent,
                "insufficient",
                (),
                "问题中没有可确认的任务名称，请补充具体任务。",
                retrieval_calls,
                diagnostics,
            )

        scoped = [
            record
            for record in self.records
            if not intent.project_candidates
            or record.source_name in intent.project_candidates
        ]
        normalized_task = normalize_lookup_text(intent.task_hint)
        matches = [
            record
            for record in scoped
            if normalize_lookup_text(record.task_name) == normalized_task
        ]
        normalized_query = intent.normalized_query
        if "父任务" in normalized_query:
            parent_matches = [record for record in matches if record.is_parent]
            if parent_matches:
                matches = parent_matches
        elif "顶层" in normalized_query:
            top_matches = [record for record in matches if record.relation == "top_level"]
            if top_matches:
                matches = top_matches

        if not matches:
            return self._result(
                intent,
                "not_found",
                (),
                f"知识库中没有找到任务“{intent.task_hint}”的可验证记录。",
                retrieval_calls,
                diagnostics,
            )

        source_count = len({record.source_name for record in matches})
        if source_count > 1:
            sources = "、".join(sorted({record.source_name for record in matches}))
            return self._result(
                intent,
                "ambiguous",
                tuple(matches),
                f"任务“{intent.task_hint}”出现在多个工程文档中，请补充工程范围：{sources}",
                retrieval_calls,
                diagnostics,
            )
        if len(matches) > 1:
            identifiers = "、".join(record.task_id for record in matches)
            return self._result(
                intent,
                "ambiguous",
                tuple(matches),
                f"同一文档中有多个“{intent.task_hint}”任务（标识号{identifiers}），请补充父子层级。",
                retrieval_calls,
                diagnostics,
            )

        record = matches[0]
        missing = [
            requested
            for requested in intent.requested_fields
            if getattr(record, requested) in (None, "")
        ]
        if missing:
            diagnostics["missing_fields"] = missing
            return self._result(
                intent,
                "insufficient",
                (record,),
                f"已找到任务“{record.task_name}”，但证据缺少字段：{', '.join(missing)}。",
                retrieval_calls,
                diagnostics,
            )

        missing_citation = [
            field_name
            for field_name, value in {
                "source_name": record.source_name,
                "task_id": record.task_id,
                "chunk_id": record.chunk_id,
            }.items()
            if not value
        ]
        if missing_citation:
            diagnostics["missing_citation_fields"] = missing_citation
            return self._result(
                intent,
                "insufficient",
                (record,),
                "已定位任务记录，但引用元数据不完整，不能输出确定答案。",
                retrieval_calls,
                diagnostics,
            )

        if retriever is not None and record.chunk_id not in hit_by_chunk:
            diagnostics["record_in_retrieved_chunks"] = False
            return self._result(
                intent,
                "insufficient",
                (record,),
                f"已定位任务“{record.task_name}”，但本次候选召回未覆盖其证据记录。",
                retrieval_calls,
                diagnostics,
            )

        if record.chunk_id:
            hit = hit_by_chunk.get(record.chunk_id)
            diagnostics["record_in_retrieved_chunks"] = hit is not None
            if hit is not None:
                diagnostics["retrieval_rank"] = hit.rank
                diagnostics["retrieval_score"] = hit.score
                diagnostics["dense_rank"] = hit.document.metadata.get("dense_rank")
                diagnostics["lexical_rank"] = hit.document.metadata.get("lexical_rank")

        return self._result(
            intent,
            "exact",
            (record,),
            self._format_answer(record, intent.requested_fields),
            retrieval_calls,
            diagnostics,
        )

    @staticmethod
    def _format_answer(
        record: ScheduleRecord,
        requested_fields: Sequence[RequestedField],
    ) -> str:
        labels = {
            "start_date": "计划开始",
            "end_date": "计划完成",
            "duration": "工期",
        }
        facts = "，".join(
            f"{labels[field_name]}{getattr(record, field_name)}"
            for field_name in requested_fields
        )
        citation = (
            f"来源：{record.source_name}，任务标识号{record.task_id}"
            + (f"，PDF第{record.page}页" if record.page is not None else "")
            + (f"，chunk_id={record.chunk_id}" if record.chunk_id else "")
        )
        return f"{record.project_title}中，“{record.task_name}”{facts}。\n{citation}"

    @staticmethod
    def _result(
        intent: QueryIntent,
        status: EvidenceStatus,
        records: tuple[ScheduleRecord, ...],
        answer: str,
        retrieval_calls: int,
        diagnostics: Mapping[str, Any],
    ) -> ReliableQueryResult:
        return ReliableQueryResult(
            intent=intent,
            status=status,
            records=records,
            answer=answer,
            retrieval_calls=retrieval_calls,
            diagnostics=diagnostics,
        )


def evaluate_reliable_lookup(
    knowledge_base: ProjectProgressKnowledgeBase,
    cases: Sequence[Mapping[str, Any]],
    *,
    retriever: RetrieverFn | None = None,
) -> dict[str, Any]:
    """Evaluate exact record/source/field behavior on labeled cases."""

    rows: list[dict[str, Any]] = []
    for case in cases:
        result = knowledge_base.lookup(str(case["query"]), retriever=retriever)
        evidence_text = "\n".join(record.raw_text for record in result.records)
        expected_status = str(case.get("expected_status", "exact"))
        expected_source = case.get("expected_source")
        expected_terms = [str(term) for term in case.get("expected_terms", [])]
        expected_fields = {
            str(key): str(value)
            for key, value in dict(case.get("expected_fields", {})).items()
        }
        status_ok = result.status == expected_status
        source_ok = expected_source is None or any(
            record.source_name == expected_source for record in result.records
        )
        terms_ok = all(term in evidence_text for term in expected_terms)
        fields_ok = all(
            any(str(getattr(record, key, "")) == value for record in result.records)
            for key, value in expected_fields.items()
        )
        rows.append(
            {
                "id": case.get("id"),
                "query": case["query"],
                "status": result.status,
                "status_ok": status_ok,
                "source_ok": source_ok,
                "terms_ok": terms_ok,
                "fields_ok": fields_ok,
                "passed": status_ok and source_ok and terms_ok and fields_ok,
                "retrieval_calls": result.retrieval_calls,
                "answer": result.answer,
            }
        )
    count = len(rows)
    return {
        "cases": count,
        "pass_rate": sum(row["passed"] for row in rows) / count if count else 0.0,
        "status_accuracy": sum(row["status_ok"] for row in rows) / count if count else 0.0,
        "source_accuracy": sum(row["source_ok"] for row in rows) / count if count else 0.0,
        "field_accuracy": sum(row["fields_ok"] for row in rows) / count if count else 0.0,
        "average_retrieval_calls": (
            sum(row["retrieval_calls"] for row in rows) / count if count else 0.0
        ),
        "rows": rows,
    }


__all__ = [
    "EvidenceStatus",
    "ProjectProgressKnowledgeBase",
    "QueryIntent",
    "ReliableQueryResult",
    "RequestedField",
    "ScheduleRecord",
    "evaluate_reliable_lookup",
    "normalize_lookup_text",
]
