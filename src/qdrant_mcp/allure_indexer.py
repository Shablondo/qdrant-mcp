"""
allure_indexer.py — индексация тест-кейсов Allure TestOps в Qdrant.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

import tiktoken

from qdrant_mcp.allure_client import AllureTestOpsClient
from qdrant_mcp.allure_qdrant_store import (
    delete_project_test_cases,
    delete_test_cases,
    ensure_collection_exists,
    upsert_test_case_chunks,
)
from qdrant_mcp.embedder import embed_texts

logger = logging.getLogger(__name__)

CHUNK_MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", "500"))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "50"))
ATTACHMENT_MAX_CHARS = int(os.environ.get("ALLURE_ATTACHMENT_MAX_CHARS", "12000"))


@dataclass
class IndexingStats:
    """Накопительная статистика индексации."""

    test_cases_indexed: int = 0
    chunks_total: int = 0
    errors: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "test_cases_indexed": self.test_cases_indexed,
            "chunks_total": self.chunks_total,
            "errors": self.errors,
        }


def _get_tokenizer():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return tiktoken.get_encoding("gpt2")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "displayName", "username", "text", "title"):
            candidate = value.get(key)
            if candidate:
                return _stringify(candidate)
        return ""
    if isinstance(value, list):
        return ", ".join(filter(None, (_stringify(item) for item in value)))
    return str(value).strip()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _stringify(value)
        if text:
            return text
    return ""


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_text(text: str, title: str = "") -> List[str]:
    if not text.strip():
        return []

    enc = _get_tokenizer()
    title_prefix = f"# {title}\n\n" if title else ""
    title_tokens = len(enc.encode(title_prefix))
    effective_max = max(50, CHUNK_MAX_TOKENS - title_tokens)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: List[str] = []
    current_paragraphs: List[str] = []
    current_tokens = 0

    for paragraph in paragraphs:
        paragraph_tokens = len(enc.encode(paragraph))

        if paragraph_tokens > effective_max:
            if current_paragraphs:
                chunks.append(title_prefix + "\n\n".join(current_paragraphs))
                overlap = current_paragraphs[-1] if current_paragraphs else ""
                current_paragraphs = [overlap] if overlap else []
                current_tokens = len(enc.encode(overlap)) if overlap else 0

            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence in sentences:
                sentence_tokens = len(enc.encode(sentence))
                if current_tokens + sentence_tokens > effective_max and current_paragraphs:
                    chunks.append(title_prefix + "\n\n".join(current_paragraphs))
                    current_paragraphs = []
                    current_tokens = 0
                current_paragraphs.append(sentence)
                current_tokens += sentence_tokens
            continue

        if current_tokens + paragraph_tokens > effective_max and current_paragraphs:
            chunks.append(title_prefix + "\n\n".join(current_paragraphs))
            overlap = current_paragraphs[-1] if current_paragraphs else ""
            if overlap and len(enc.encode(overlap)) < CHUNK_OVERLAP_TOKENS:
                current_paragraphs = [overlap]
                current_tokens = len(enc.encode(overlap))
            else:
                current_paragraphs = []
                current_tokens = 0

        current_paragraphs.append(paragraph)
        current_tokens += paragraph_tokens

    if current_paragraphs:
        chunks.append(title_prefix + "\n\n".join(current_paragraphs))

    return chunks


def _extract_test_case_id(summary: Dict[str, Any]) -> Optional[int]:
    raw_id = summary.get("id") or summary.get("testCaseId")
    if raw_id is None:
        return None
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _extract_owner(test_case: Dict[str, Any]) -> str:
    return _first_non_empty(
        test_case.get("owner"),
        test_case.get("responsible"),
        test_case.get("lastModifiedBy"),
        test_case.get("createdBy"),
    )


def _extract_status(test_case: Dict[str, Any]) -> str:
    return _first_non_empty(
        test_case.get("statusName"),
        test_case.get("status"),
        test_case.get("workflowStatus"),
    )


def _extract_updated_at(test_case: Dict[str, Any]) -> str:
    return _first_non_empty(
        test_case.get("updatedDate"),
        test_case.get("lastModifiedDate"),
        test_case.get("modifiedDate"),
        test_case.get("createdDate"),
    )


def _extract_project_id(test_case: Dict[str, Any], fallback_project_id: int) -> str:
    return _first_non_empty(
        test_case.get("projectId"),
        test_case.get("project", {}).get("id") if isinstance(test_case.get("project"), dict) else "",
        fallback_project_id,
    )


def _collect_tag_names(tags: List[Dict[str, Any]]) -> List[str]:
    names = sorted(
        {
            _first_non_empty(tag.get("name"), tag.get("value"), tag)
            for tag in tags
            if _first_non_empty(tag.get("name"), tag.get("value"), tag)
        }
    )
    return names


def _iter_scenario_nodes(nodes: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(nodes, dict):
        values = [value for value in nodes.values() if isinstance(value, dict)]
        return sorted(
            values,
            key=lambda item: (
                item.get("position", item.get("index", item.get("order", 0))),
                item.get("id", 0),
            ),
        )
    if isinstance(nodes, list):
        return [value for value in nodes if isinstance(value, dict)]
    return []


def _flatten_scenario_steps(scenario: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    def _walk(nodes: Any, depth: int = 0) -> None:
        for index, step in enumerate(_iter_scenario_nodes(nodes), start=1):
            body = _first_non_empty(step.get("body"), step.get("name"), step.get("text"))
            expected = _first_non_empty(
                step.get("expectedResult"),
                step.get("expected"),
                step.get("result"),
            )
            prefix = "  " * depth + f"{index}."
            if body:
                lines.append(f"{prefix} {body}")
            if expected:
                lines.append(f"{'  ' * depth}   Expected: {expected}")
            children = (
                step.get("steps")
                or step.get("scenarioSteps")
                or step.get("children")
                or []
            )
            if children:
                _walk(children, depth + 1)

    root_nodes = scenario.get("scenarioSteps") or scenario.get("steps") or []
    _walk(root_nodes)
    return lines


def _looks_textual(content_type: str, text: str, filename: str) -> bool:
    lowered_type = (content_type or "").lower()
    lowered_name = filename.lower()
    if any(
        marker in lowered_type
        for marker in ("text/", "json", "xml", "yaml", "javascript", "html", "csv")
    ):
        return True
    if any(
        lowered_name.endswith(ext)
        for ext in (
            ".txt",
            ".md",
            ".json",
            ".xml",
            ".yaml",
            ".yml",
            ".csv",
            ".log",
            ".sql",
            ".html",
        )
    ):
        return True
    if not text:
        return False
    sample = text[:512]
    bad_chars = sum(
        1
        for char in sample
        if ord(char) == 65533 or ord(char) < 9 or (13 < ord(char) < 32)
    )
    return (bad_chars / max(len(sample), 1)) < 0.05


def _build_overview_section(
    test_case_id: int,
    test_case: Dict[str, Any],
    tag_names: List[str],
    project_id: str,
) -> str:
    name = _first_non_empty(test_case.get("name"), f"Test Case {test_case_id}")
    description = _first_non_empty(
        test_case.get("description"),
        test_case.get("fullName"),
        test_case.get("text"),
    )
    preconditions = _first_non_empty(
        test_case.get("precondition"),
        test_case.get("preconditions"),
        test_case.get("before"),
    )
    lines = [
        f"Test case ID: {test_case_id}",
        f"Project ID: {project_id}",
        f"Name: {name}",
    ]

    status = _extract_status(test_case)
    if status:
        lines.append(f"Status: {status}")

    owner = _extract_owner(test_case)
    if owner:
        lines.append(f"Owner: {owner}")

    updated_at = _extract_updated_at(test_case)
    if updated_at:
        lines.append(f"Updated at: {updated_at}")

    priority = _first_non_empty(test_case.get("priority"), test_case.get("priorityName"))
    if priority:
        lines.append(f"Priority: {priority}")

    test_layer = _first_non_empty(test_case.get("testLayer"), test_case.get("layer"))
    if test_layer:
        lines.append(f"Test layer: {test_layer}")

    if tag_names:
        lines.append(f"Tags: {', '.join(tag_names)}")

    body_parts = ["\n".join(lines)]
    if description:
        body_parts.append(f"Description:\n{description}")
    if preconditions:
        body_parts.append(f"Preconditions:\n{preconditions}")

    return _normalize_whitespace("\n\n".join(body_parts))


def _build_attachment_sections(attachments: List[Dict[str, Any]]) -> List[str]:
    sections: List[str] = []
    for attachment in attachments:
        name = _first_non_empty(attachment.get("name"), f"Attachment {attachment.get('id', '')}")
        content = attachment.get("content", {})
        content_text = _stringify(content.get("text"))
        content_type = _stringify(content.get("content_type"))
        if not _looks_textual(content_type, content_text, name):
            sections.append(
                _normalize_whitespace(
                    f"Attachment: {name}\nContent-Type: {content_type or 'unknown'}\n"
                    "Content omitted: non-text attachment."
                )
            )
            continue

        if len(content_text) > ATTACHMENT_MAX_CHARS:
            content_text = content_text[:ATTACHMENT_MAX_CHARS] + "\n...[truncated]..."

        sections.append(
            _normalize_whitespace(
                f"Attachment: {name}\nContent-Type: {content_type or 'unknown'}\n\n{content_text}"
            )
        )
    return [section for section in sections if section]


def _build_chunks(
    test_case_id: int,
    payload: Dict[str, Any],
    project_id: int,
) -> Dict[str, Any]:
    test_case = payload.get("test_case", {}) if isinstance(payload.get("test_case"), dict) else {}
    scenario = payload.get("scenario", {}) if isinstance(payload.get("scenario"), dict) else {}
    attachments = payload.get("attachments", []) if isinstance(payload.get("attachments"), list) else []
    tags = payload.get("tags", []) if isinstance(payload.get("tags"), list) else []

    name = _first_non_empty(test_case.get("name"), f"Test Case {test_case_id}")
    resolved_project_id = _extract_project_id(test_case, project_id)
    tag_names = _collect_tag_names(tags)

    sections: List[Dict[str, str]] = []

    overview = _build_overview_section(test_case_id, test_case, tag_names, resolved_project_id)
    if overview:
        sections.extend(
            {"chunk_type": "overview", "text": chunk}
            for chunk in _chunk_text(overview, title=name)
        )

    scenario_lines = _flatten_scenario_steps(scenario)
    if scenario_lines:
        scenario_text = _normalize_whitespace("Scenario:\n" + "\n".join(scenario_lines))
        sections.extend(
            {"chunk_type": "scenario", "text": chunk}
            for chunk in _chunk_text(scenario_text, title=name)
        )

    if tag_names:
        tags_text = _normalize_whitespace("Tags:\n" + "\n".join(f"- {tag}" for tag in tag_names))
        sections.extend(
            {"chunk_type": "tags", "text": chunk}
            for chunk in _chunk_text(tags_text, title=name)
        )

    for attachment_text in _build_attachment_sections(attachments):
        sections.extend(
            {"chunk_type": "attachment", "text": chunk}
            for chunk in _chunk_text(attachment_text, title=name)
        )

    metadata = {
        "project_id": resolved_project_id,
        "name": name,
        "status": _extract_status(test_case),
        "owner": _extract_owner(test_case),
        "updated_at": _extract_updated_at(test_case),
        "tags": tag_names,
        "source": "allure",
    }

    return {"chunks": sections, "metadata": metadata, "name": name}


def index_one_test_case(
    client: AllureTestOpsClient,
    test_case_id: int,
    project_id: int,
    full_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Индексирует один тест-кейс, заменяя его предыдущую версию в Qdrant."""
    payload = full_payload or client.get_complete_test_case(test_case_id)
    normalized = _build_chunks(test_case_id, payload, project_id)
    chunks = normalized["chunks"]
    if not chunks:
        return {"test_case_id": str(test_case_id), "chunks_total": 0, "name": normalized["name"]}

    content_vectors = embed_texts([chunk["text"] for chunk in chunks])
    title_vectors = embed_texts([normalized["name"]] * len(chunks))
    inserted = upsert_test_case_chunks(
        test_case_id=str(test_case_id),
        chunks=chunks,
        content_vectors=content_vectors,
        title_vectors=title_vectors,
        metadata=normalized["metadata"],
    )
    return {"test_case_id": str(test_case_id), "chunks_total": inserted, "name": normalized["name"]}


def run_index(
    *,
    project_id: Optional[int] = None,
    rql: Optional[str] = None,
    page_size: int = 100,
    max_test_cases: Optional[int] = None,
    reindex: bool = False,
) -> Dict[str, Any]:
    """
    Индексирует тест-кейсы Allure TestOps.
    """
    stats = IndexingStats()
    ensure_collection_exists()

    with AllureTestOpsClient() as client:
        resolved_project_id = project_id or client.default_project_id
        if resolved_project_id is None:
            raise ValueError(
                "project_id не передан и ALLURE_TESTOPS_PROJECT_ID не задан"
            )

        summaries = client.list_test_cases(
            project_id=resolved_project_id,
            rql=rql,
            page_size=page_size,
            max_test_cases=max_test_cases,
        )
        test_case_ids = [
            str(test_case_id)
            for summary in summaries
            if (test_case_id := _extract_test_case_id(summary)) is not None
        ]

        if reindex:
            if rql:
                delete_test_cases(test_case_ids)
            else:
                delete_project_test_cases(str(resolved_project_id))

        for summary in summaries:
            test_case_id = _extract_test_case_id(summary)
            if test_case_id is None:
                logger.warning("Пропуск test case без id: %s", summary)
                stats.errors += 1
                continue

            try:
                result = index_one_test_case(client, test_case_id, resolved_project_id)
                if not result["chunks_total"]:
                    logger.warning("Тест-кейс %s не содержит индексируемого контента", test_case_id)
                    continue
                stats.test_cases_indexed += 1
                stats.chunks_total += result["chunks_total"]
            except Exception as exc:
                logger.error("Ошибка индексации test_case=%s: %s", test_case_id, exc, exc_info=True)
                stats.errors += 1

    result = stats.as_dict()
    result.update(
        {
            "project_id": resolved_project_id,
            "rql": rql,
            "reindex": reindex,
            "fetched_test_cases": len(summaries),
        }
    )
    return result
