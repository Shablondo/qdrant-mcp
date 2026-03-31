"""
embedder.py — клиент к OpenAI-compatible Embeddings API.
Модель: copilot-embed-4b, размерность: 2560

Основная переменная для ключа: COPILOT_API_KEY.

Конфигурация через переменные окружения:

  Вариант 1 — полный URL до endpoint (рекомендуется):
    EMBED_API_ENDPOINT=https://api-copilot.x5.ru/aigw/v1/embeddings
    В этом случае EMBED_API_BASE игнорируется.

  Вариант 2 — базовый URL (openai SDK добавит /embeddings автоматически):
    EMBED_API_BASE=https://api-copilot.x5.ru/aigw/v1
"""

import logging
import os
from typing import List

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

EMBED_API_ENDPOINT = os.environ.get("EMBED_API_ENDPOINT", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "")
COPILOT_API_KEY = os.environ.get("COPILOT_API_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "copilot-embed-4b")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "2560"))
EMBED_BATCH_SIZE = 32


def _resolve_base_url() -> str:
    """Определяет base_url для OpenAI клиента."""
    if EMBED_API_ENDPOINT:
        base = EMBED_API_ENDPOINT.rstrip("/")
        if base.endswith("/embeddings"):
            base = base[: -len("/embeddings")]
        logger.debug("EMBED_API_ENDPOINT=%s -> base_url=%s", EMBED_API_ENDPOINT, base)
        return base

    if EMBED_API_BASE:
        return EMBED_API_BASE.rstrip("/")

    raise ValueError(
        "Не задан ни EMBED_API_ENDPOINT, ни EMBED_API_BASE в переменных окружения. "
        "Пример: EMBED_API_ENDPOINT=https://api-copilot.x5.ru/aigw/v1/embeddings"
    )


def _get_client() -> OpenAI:
    """Создаёт OpenAI клиент с корпоративным endpoint."""
    if not COPILOT_API_KEY:
        raise ValueError("COPILOT_API_KEY не задан в переменных окружения")

    return OpenAI(
        api_key=COPILOT_API_KEY,
        base_url=_resolve_base_url(),
        http_client=httpx.Client(verify=False),
    )


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Получает эмбеддинги для списка текстов."""
    if not texts:
        return []

    client = _get_client()
    all_embeddings: List[List[float]] = []

    for index in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[index : index + EMBED_BATCH_SIZE]
        logger.debug(
            "Получение эмбеддингов для батча %s, размер: %s",
            index // EMBED_BATCH_SIZE + 1,
            len(batch),
        )

        response = client.embeddings.create(model=EMBED_MODEL, input=batch)
        sorted_data = sorted(response.data, key=lambda item: item.index)
        batch_embeddings = [item.embedding for item in sorted_data]

        if batch_embeddings and len(batch_embeddings[0]) != EMBED_DIMENSIONS:
            logger.warning(
                "Размерность эмбеддинга %s не совпадает с ожидаемой %s",
                len(batch_embeddings[0]),
                EMBED_DIMENSIONS,
            )

        all_embeddings.extend(batch_embeddings)

    return all_embeddings


def embed_single(text: str) -> List[float]:
    """Получает эмбеддинг для одного текста."""
    results = embed_texts([text])
    if not results:
        raise RuntimeError("Не удалось получить эмбеддинг")
    return results[0]
