"""
confluence_utils.py — Утилиты для работы с Confluence API и обработки контента.

Содержит:
- HTTP клиент с авторизацией Confluence
- Функции получения страниц и дочерних страниц
- Конвертация HTML в plain text
- Разбивка текста на чанки по токенам
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import html2text
import httpx
import tiktoken
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Конфигурация Confluence
CONFLUENCE_URL = os.environ.get("CONFLUENCE_URL", "").rstrip("/")
CONFLUENCE_PERSONAL_TOKEN = os.environ.get("CONFLUENCE_PERSONAL_TOKEN", "")
CONFLUENCE_SSL_VERIFY = os.environ.get("CONFLUENCE_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# Задержка между запросами к Confluence API (в мс) — чтобы не триггерить WAF/rate-limiter
_CONFLUENCE_DELAY = float(os.environ.get("CONFLUENCE_REQUEST_DELAY_MS", "50")) / 1000

# Параметры чанкинга
CHUNK_MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", "500"))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "50"))


def _get_tokenizer():
    """Возвращает токенизатор cl100k_base (GPT-4 совместимый)."""
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return tiktoken.get_encoding("gpt2")


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


def _html_to_text(html_content: str) -> str:
    """Конвертирует HTML Confluence в чистый текст."""
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup.find_all(["style", "script", "nav"]):
        tag.decompose()

    for tag in soup.find_all(class_=re.compile(r"(toc|breadcrumb|page-metadata|page-restrict)")):
        tag.decompose()

    cleaned_html = str(soup)

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.body_width = 0
    converter.unicode_snob = True
    converter.skip_internal_links = True

    text = converter.handle(cleaned_html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def _chunk_text(text: str, page_title: str = "") -> list[str]:
    """Разбивает текст на чанки по количеству токенов."""
    if not text.strip():
        return []

    enc = _get_tokenizer()

    title_prefix = f"# {page_title}\n\n" if page_title else ""
    title_tokens = len(enc.encode(title_prefix))
    effective_max = CHUNK_MAX_TOKENS - title_tokens

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current_chunk_paragraphs: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = len(enc.encode(para))

        if para_tokens > effective_max:
            if current_chunk_paragraphs:
                chunk_text = "\n\n".join(current_chunk_paragraphs)
                chunks.append(title_prefix + chunk_text)
                current_chunk_paragraphs = current_chunk_paragraphs[-1:] if current_chunk_paragraphs else []
                current_tokens = sum(len(enc.encode(p)) for p in current_chunk_paragraphs)

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

        if current_tokens + para_tokens > effective_max and current_chunk_paragraphs:
            chunk_text = "\n\n".join(current_chunk_paragraphs)
            chunks.append(title_prefix + chunk_text)

            overlap_para = current_chunk_paragraphs[-1] if current_chunk_paragraphs else ""
            if overlap_para and len(enc.encode(overlap_para)) < CHUNK_OVERLAP_TOKENS:
                current_chunk_paragraphs = [overlap_para]
                current_tokens = len(enc.encode(overlap_para))
            else:
                current_chunk_paragraphs = []
                current_tokens = 0

        current_chunk_paragraphs.append(para)
        current_tokens += para_tokens

    if current_chunk_paragraphs:
        chunk_text = "\n\n".join(current_chunk_paragraphs)
        chunks.append(title_prefix + chunk_text)

    return chunks


def _fetch_page(client: httpx.Client, page_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Получает страницу Confluence по ID.

    Returns:
        (page_dict, None) при успехе.
        (None, error_message) при ошибке.
    """
    url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}"
    params = {
        "expand": "body.storage,version,space",
        "status": "current",
    }

    try:
        time.sleep(_CONFLUENCE_DELAY)
        response = client.get(url, params=params)
        response.raise_for_status()
        if not response.text.strip():
            return None, f"HTTP {response.status_code}: empty response body"
        data = response.json()
    except httpx.HTTPStatusError as e:
        message = f"HTTP {e.response.status_code}"
        logger.warning("HTTP ошибка при получении страницы %s: %s", page_id, e.response.status_code)
        return None, message
    except json.JSONDecodeError:
        preview = response.text[:200].replace("\n", " ")
        message = f"HTTP {response.status_code}: invalid JSON, body: «{preview}»"
        logger.warning("Не-JSON ответ при получении страницы %s: %s", page_id, preview)
        return None, message
    except Exception as e:
        message = str(e) or type(e).__name__
        logger.error("Ошибка при получении страницы %s: %s", page_id, e)
        return None, message

    body_html = ""
    try:
        body_html = data.get("body", {}).get("storage", {}).get("value", "")
    except (KeyError, TypeError, AttributeError):
        pass

    space_key = ""
    try:
        space_key = data.get("space", {}).get("key", "")
    except (KeyError, TypeError, AttributeError):
        pass

    last_modified = ""
    version = ""
    try:
        version_data = data.get("version", {})
        last_modified = version_data.get("when", "")
        version = str(version_data.get("number", ""))
    except (KeyError, TypeError, AttributeError):
        pass

    page_url = f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}"
    try:
        links = data.get("_links", {})
        webui = links.get("webui", "")
        base = links.get("base", CONFLUENCE_URL)
        if webui:
            page_url = f"{base}{webui}"
    except (KeyError, TypeError, AttributeError):
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


def _fetch_child_pages(client: httpx.Client, page_id: str) -> list[str]:
    """Возвращает список ID дочерних страниц."""
    child_ids: list[str] = []
    start = 0
    limit = 50

    while True:
        url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}/child/page"
        params = {"start": start, "limit": limit, "expand": "version"}

        try:
            time.sleep(_CONFLUENCE_DELAY)
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("Ошибка при получении дочерних страниц для %s: %s", page_id, e)
            break

        results = data.get("results", [])
        for child in results:
            child_ids.append(child.get("id", ""))

        size = data.get("size", 0)
        if size < limit:
            break
        start += limit

    return [cid for cid in child_ids if cid]
