"""
embedder.py — клиент к OpenAI-compatible Embeddings API.
Модель по умолчанию: text-embedding-3-large, размерность: 2560

Основная переменная для ключа: OPENAI_API_KEY.
Для обратной совместимости также поддерживается COPILOT_API_KEY.

Конфигурация через переменные окружения:

  Вариант 1 — полный URL до endpoint:
    EMBED_API_ENDPOINT=https://api.openai.com/v1/embeddings
    В этом случае EMBED_API_BASE игнорируется.

  Вариант 2 — базовый URL (openai SDK добавит /embeddings автоматически):
    EMBED_API_BASE=https://api.openai.com/v1
"""

import functools
import logging
import os
from typing import Any, Iterable, List

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

EMBED_API_ENDPOINT = os.environ.get("EMBED_API_ENDPOINT", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("COPILOT_API_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "2560"))
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "32"))


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
        "Пример: EMBED_API_ENDPOINT=https://api.openai.com/v1/embeddings"
    )


@functools.lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    """Создаёт OpenAI клиент для embeddings API."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY не задан в переменных окружения")

    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=_resolve_base_url(),
        http_client=httpx.Client(verify=False),
    )


class EmbedResponseError(RuntimeError):
    """Embedder вернул объект неожиданного типа (например, строку вместо CreateEmbeddingResponse)."""


def _extract_embeddings_from_response(response: Any, batch_size: int) -> List[List[float]]:
    if not hasattr(response, "data") or not isinstance(response.data, Iterable):
        raise EmbedResponseError(
            f"embedder returned unexpected response: type={type(response).__name__} "
            f"response={response} batch_size={batch_size}"
        )
    try:
        sorted_data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in sorted_data]
    except AttributeError as exc:
        raise EmbedResponseError(
            f"embedder response data attribute error: type={type(exc).__name__} "
            f"message={exc} batch_size={batch_size}"
            f" response_type={type(response).__name__} data_type={type(response.data).__name__}"
        ) from exc


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

        try:
            response = client.embeddings.create(model=EMBED_MODEL, input=batch)
        except Exception as exc:
            raise EmbedResponseError(
                f"embedder API call failed: type={type(exc).__name__} "
                f"message={exc} batch_size={len(batch)}"
            ) from exc
        try:
            batch_embeddings = _extract_embeddings_from_response(response, len(batch))
        except EmbedResponseError:
            raise
        except Exception as exc:
            raise EmbedResponseError(
                f"embedder response extraction failed: type={type(exc).__name__} "
                f"message={exc} batch_size={len(batch)} response_type={type(response).__name__}"
            ) from exc

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
