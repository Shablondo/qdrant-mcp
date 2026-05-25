"""
indexer.py — рекурсивная индексация страниц Confluence в Qdrant.

Процесс для каждой страницы:
1. Получить HTML контент через Confluence REST API
2. Конвертировать HTML → plain text
3. Разбить на чанки (max 500 токенов, overlap 50)
4. Получить эмбеддинги через embedder.py
5. Сохранить в Qdrant через qdrant_store.py
6. Рекурсивно обработать дочерние страницы
"""

from dataclasses import dataclass
import os
import logging
import re
from typing import List, Dict, Any, Optional, Tuple

import httpx
import tiktoken
from bs4 import BeautifulSoup
import html2text

from qdrant_mcp.embedder import embed_single, embed_texts
from qdrant_mcp.qdrant_store import upsert_page_chunks, ensure_collection_exists

logger = logging.getLogger(__name__)

# Конфигурация Confluence
CONFLUENCE_URL = os.environ.get("CONFLUENCE_URL", "").rstrip("/")
CONFLUENCE_PERSONAL_TOKEN = os.environ.get("CONFLUENCE_PERSONAL_TOKEN", "")
CONFLUENCE_SSL_VERIFY = os.environ.get("CONFLUENCE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# Параметры чанкинга
CHUNK_MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", "500"))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "50"))

@dataclass
class IndexingStats:
    """Накопительная статистика индексации."""

    pages_indexed: int = 0
    chunks_total: int = 0
    errors: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "pages_indexed": self.pages_indexed,
            "chunks_total": self.chunks_total,
            "errors": self.errors,
        }


def _get_http_client() -> httpx.Client:
    """Создаёт HTTP клиент с авторизацией Confluence."""
    if not CONFLUENCE_URL:
        raise ValueError("CONFLUENCE_URL не задан")
    if not CONFLUENCE_PERSONAL_TOKEN:
        raise ValueError("CONFLUENCE_PERSONAL_TOKEN не задан")

    return httpx.Client(
        headers={
            "Authorization": f"Bearer {CONFLUENCE_PERSONAL_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        verify=CONFLUENCE_SSL_VERIFY,
        timeout=30.0,
        follow_redirects=True,
    )


def _get_tokenizer():
    """Возвращает токенизатор cl100k_base (GPT-4 совместимый)."""
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return tiktoken.get_encoding("gpt2")


def _html_to_text(html_content: str) -> str:
    """
    Конвертирует HTML Confluence в чистый текст.
    Использует html2text с настройками для читаемости.
    """
    # Сначала убираем Confluence-специфичные макросы через BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")

    # Убираем навигационные и служебные блоки
    for tag in soup.find_all(["style", "script", "nav"]):
        tag.decompose()

    # Убираем Confluence-специфичные макросы
    for tag in soup.find_all(class_=re.compile(r"(toc|breadcrumb|page-metadata|page-restrict)")):
        tag.decompose()

    cleaned_html = str(soup)

    # Конвертируем в markdown-подобный текст
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.body_width = 0  # Не переносить строки
    converter.unicode_snob = True
    converter.skip_internal_links = True

    text = converter.handle(cleaned_html)

    # Очищаем лишние пустые строки
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def _chunk_text(text: str, page_title: str = "") -> List[str]:
    """
    Разбивает текст на чанки по количеству токенов.
    Старается разбивать по границам абзацев.

    Args:
        text: Текст для разбивки.
        page_title: Заголовок страницы (добавляется к каждому чанку).

    Returns:
        Список текстовых чанков.
    """
    if not text.strip():
        return []

    enc = _get_tokenizer()

    # Добавляем заголовок как контекст к каждому чанку
    title_prefix = f"# {page_title}\n\n" if page_title else ""
    title_tokens = len(enc.encode(title_prefix))
    effective_max = CHUNK_MAX_TOKENS - title_tokens

    # Разбиваем по абзацам
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    current_chunk_paragraphs: List[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = len(enc.encode(para))

        # Если абзац сам по себе больше лимита — разбиваем по предложениям
        if para_tokens > effective_max:
            if current_chunk_paragraphs:
                chunk_text = "\n\n".join(current_chunk_paragraphs)
                chunks.append(title_prefix + chunk_text)
                # Overlap: оставляем последний абзац
                current_chunk_paragraphs = current_chunk_paragraphs[-1:] if current_chunk_paragraphs else []
                current_tokens = sum(len(enc.encode(p)) for p in current_chunk_paragraphs)

            # Разбиваем длинный абзац по предложениям
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                sent_tokens = len(enc.encode(sentence))
                if current_tokens + sent_tokens > effective_max and current_chunk_paragraphs:
                    chunk_text = "\n\n".join(current_chunk_paragraphs)
                    chunks.append(title_prefix + chunk_text)
                    current_chunk_paragraphs = []
                    current_tokens = 0
                current_chunk_paragraphs.append(sentence)
                current_tokens += sent_tokens
            continue

        # Если добавление абзаца превысит лимит — сохраняем текущий чанк
        if current_tokens + para_tokens > effective_max and current_chunk_paragraphs:
            chunk_text = "\n\n".join(current_chunk_paragraphs)
            chunks.append(title_prefix + chunk_text)

            # Overlap: оставляем последний абзац из текущего чанка
            overlap_para = current_chunk_paragraphs[-1] if current_chunk_paragraphs else ""
            if overlap_para and len(enc.encode(overlap_para)) < CHUNK_OVERLAP_TOKENS:
                current_chunk_paragraphs = [overlap_para]
                current_tokens = len(enc.encode(overlap_para))
            else:
                current_chunk_paragraphs = []
                current_tokens = 0

        current_chunk_paragraphs.append(para)
        current_tokens += para_tokens

    # Последний чанк
    if current_chunk_paragraphs:
        chunk_text = "\n\n".join(current_chunk_paragraphs)
        chunks.append(title_prefix + chunk_text)

    return chunks


def _fetch_page(client: httpx.Client, page_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Получает страницу Confluence по ID.

    Returns:
        (page_dict, None) при успехе, где page_dict содержит:
          id, title, body_html, url, space_key, last_modified, version
        (None, error_message) при ошибке.
    """
    url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}"
    params = {
        "expand": "body.storage,version,space",
        "status": "current",
    }

    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        message = f"HTTP {e.response.status_code}"
        logger.warning(f"HTTP ошибка при получении страницы {page_id}: {e.response.status_code}")
        return None, message
    except Exception as e:
        message = str(e) or type(e).__name__
        logger.error(f"Ошибка при получении страницы {page_id}: {e}")
        return None, message

    body_html = ""
    try:
        body_html = data.get("body", {}).get("storage", {}).get("value", "")
    except Exception:
        pass

    space_key = ""
    try:
        space_key = data.get("space", {}).get("key", "")
    except Exception:
        pass

    last_modified = ""
    version = ""
    try:
        version_data = data.get("version", {})
        last_modified = version_data.get("when", "")
        version = str(version_data.get("number", ""))
    except Exception:
        pass

    page_url = f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}"
    try:
        links = data.get("_links", {})
        webui = links.get("webui", "")
        base = links.get("base", CONFLUENCE_URL)
        if webui:
            page_url = f"{base}{webui}"
    except Exception:
        pass

    return {
        "id": page_id,
        "title": data.get("title", ""),
        "body_html": body_html,
        "url": page_url,
        "space_key": space_key,
        "last_modified": last_modified,
        "version": version,
    }, None


def _fetch_child_pages(client: httpx.Client, page_id: str) -> List[str]:
    """
    Возвращает список ID дочерних страниц.

    Args:
        page_id: ID родительской страницы.

    Returns:
        Список ID дочерних страниц.
    """
    child_ids = []
    start = 0
    limit = 50

    while True:
        url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}/child/page"
        params = {"start": start, "limit": limit, "expand": "version"}

        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning(f"Ошибка при получении дочерних страниц для {page_id}: {e}")
            break

        results = data.get("results", [])
        for child in results:
            child_ids.append(child.get("id", ""))

        # Проверяем пагинацию
        size = data.get("size", 0)
        if size < limit:
            break
        start += limit

    return [cid for cid in child_ids if cid]


def _build_title_vectors(title: str, chunks_count: int) -> List[List[float]]:
    """Возвращает один и тот же title-вектор для всех чанков страницы."""
    if not title or chunks_count == 0:
        return []

    title_vector = embed_single(title)
    return [title_vector] * chunks_count


def index_page_recursive(
    page_id: str,
    root_page_id: str,
    client: httpx.Client,
    stats: IndexingStats,
    visited: set,
    depth: int = 0,
    max_depth: int = 20,
) -> None:
    """
    Рекурсивно индексирует страницу и все её дочерние страницы.

    Args:
        page_id: ID текущей страницы.
        root_page_id: ID корневой страницы (для поля root_page_id в payload).
        client: HTTP клиент Confluence.
        stats: Словарь для накопления статистики (pages_indexed, chunks_total, errors).
        visited: Множество уже обработанных page_id (защита от циклов).
        depth: Текущая глубина рекурсии.
        max_depth: Максимальная глубина обхода.
    """
    if page_id in visited:
        logger.debug(f"Страница {page_id} уже обработана, пропускаем")
        return

    if depth > max_depth:
        logger.warning(f"Достигнута максимальная глубина {max_depth} для страницы {page_id}")
        return

    visited.add(page_id)
    indent = "  " * depth

    logger.info(f"{indent}Индексация страницы {page_id} (глубина {depth})")

    # Получаем страницу
    page, _fetch_error = _fetch_page(client, page_id)
    if not page:
        logger.warning(f"{indent}Не удалось получить страницу {page_id}, пропускаем")
        stats.errors += 1
        return

    title = page["title"]
    logger.info(f"{indent}  Страница: {title}")

    # Конвертируем HTML в текст
    if page["body_html"]:
        plain_text = _html_to_text(page["body_html"])
    else:
        plain_text = ""
        logger.warning(f"{indent}  Страница {page_id} имеет пустой контент")

    # Разбиваем на чанки
    chunks = _chunk_text(plain_text, title) if plain_text else []
    logger.info(f"{indent}  Чанков: {len(chunks)}")

    content_vectors = embed_texts(chunks) if chunks else []
    title_vectors = _build_title_vectors(title, len(chunks))
    # Сохраняем в Qdrant с named vectors
    metadata = {
        "title": title,
        "url": page["url"],
        "space_key": page["space_key"],
        "root_page_id": root_page_id,
        "last_modified": page["last_modified"],
    }

    inserted = upsert_page_chunks(
        page_id=page_id,
        chunks=chunks,
        content_vectors=content_vectors,
        title_vectors=title_vectors,
        metadata=metadata,
    )

    stats.pages_indexed += 1
    stats.chunks_total += inserted

    # Получаем и обрабатываем дочерние страницы
    child_ids = _fetch_child_pages(client, page_id)
    logger.info(f"{indent}  Дочерних страниц: {len(child_ids)}")

    for child_id in child_ids:
        index_page_recursive(
            page_id=child_id,
            root_page_id=root_page_id,
            client=client,
            stats=stats,
            visited=visited,
            depth=depth + 1,
            max_depth=max_depth,
        )


def run_index(page_id: str) -> Dict[str, Any]:
    """
    Запускает полную индексацию страницы и всего её дерева дочерних страниц.

    Args:
        page_id: ID корневой страницы Confluence.

    Returns:
        Словарь со статистикой: pages_indexed, chunks_total, errors, root_page_id.
    """
    logger.info(f"Начало индексации дерева страниц от {page_id}")
    ensure_collection_exists()

    stats = IndexingStats()
    visited: set = set()

    with _get_http_client() as client:
        index_page_recursive(
            page_id=page_id,
            root_page_id=page_id,
            client=client,
            stats=stats,
            visited=visited,
        )

    logger.info(
        f"Индексация завершена: "
        f"{stats.pages_indexed} страниц, "
        f"{stats.chunks_total} чанков, "
        f"{stats.errors} ошибок"
    )

    return {
        "root_page_id": page_id,
        **stats.as_dict(),
    }
