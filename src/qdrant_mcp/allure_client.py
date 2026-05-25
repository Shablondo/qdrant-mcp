"""
allure_client.py — синхронный клиент для Allure TestOps API.

Используется индексатором тест-кейсов для получения полного контекста:
test case -> scenario -> attachments -> tags.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

# `.env` используется только как опциональный fallback.
# Переменные окружения процесса/контейнера имеют приоритет.
load_dotenv(override=False)

logger = logging.getLogger(__name__)

ALLURE_TESTOPS_URL = os.environ.get("ALLURE_TESTOPS_URL", "").rstrip("/")
ALLURE_TESTOPS_API_TOKEN = os.environ.get("ALLURE_TESTOPS_API_TOKEN", "")
ALLURE_TESTOPS_PROJECT_ID = os.environ.get("ALLURE_TESTOPS_PROJECT_ID")
ALLURE_TESTOPS_TIMEOUT = int(os.environ.get("ALLURE_TESTOPS_TIMEOUT", "30"))
ALLURE_TESTOPS_SSL_VERIFY = os.environ.get("ALLURE_TESTOPS_SSL_VERIFY", "true").lower() not in (
    "false",
    "0",
    "no",
)


class AllureTestOpsError(Exception):
    """Ошибка взаимодействия с Allure TestOps API."""


def _parse_default_project_id() -> Optional[int]:
    if not ALLURE_TESTOPS_PROJECT_ID:
        return None
    try:
        return int(ALLURE_TESTOPS_PROJECT_ID)
    except ValueError:
        return None


def _compact_params(**kwargs: Any) -> Dict[str, Any]:
    """Удаляет пустые query-параметры."""
    return {key: value for key, value in kwargs.items() if value is not None}


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    """Достаёт список элементов из paginated-ответа Allure."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("content", "items", "results", "data", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested_items = _extract_items(value)
            if nested_items:
                return nested_items

    return []


_thread_local = threading.local()


def _get_thread_client() -> httpx.Client:
    if not hasattr(_thread_local, "client"):
        _thread_local.client = httpx.Client(
            headers={
                "Authorization": f"Api-Token {ALLURE_TESTOPS_API_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=ALLURE_TESTOPS_TIMEOUT,
            verify=ALLURE_TESTOPS_SSL_VERIFY,
            follow_redirects=True,
        )
    return _thread_local.client


class AllureTestOpsClient:
    """Минималистичный sync-клиент для индексатора."""

    def __init__(self) -> None:
        if not ALLURE_TESTOPS_URL:
            raise ValueError("ALLURE_TESTOPS_URL не задан")
        if not ALLURE_TESTOPS_API_TOKEN:
            raise ValueError("ALLURE_TESTOPS_API_TOKEN не задан")

        self.default_project_id = _parse_default_project_id()
        self._client = _get_thread_client()

    def close(self) -> None:
        """Закрывает underlying HTTP client (no-op — thread-local client is reused)."""
        pass

    def __enter__(self) -> "AllureTestOpsClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        return_raw: bool = False,
    ) -> Any:
        url = f"{ALLURE_TESTOPS_URL}{endpoint}"
        response = self._client.request(method=method, url=url, params=params)

        if response.status_code in (401, 403):
            raise AllureTestOpsError(
                "Ошибка аутентификации Allure TestOps. Проверьте URL и API токен."
            )
        if response.status_code == 404:
            raise AllureTestOpsError(f"Ресурс {endpoint} не найден")
        if response.status_code >= 400:
            raise AllureTestOpsError(
                f"Ошибка Allure TestOps API {response.status_code}: {response.text}"
            )

        if return_raw:
            return {
                "text": response.text,
                "content_type": response.headers.get("content-type", ""),
                "content_length": response.headers.get("content-length"),
            }

        try:
            return response.json()
        except Exception as exc:
            raise AllureTestOpsError(
                f"Не удалось декодировать JSON для {endpoint}: {exc}"
            ) from exc

    def list_test_cases(
        self,
        *,
        project_id: Optional[int] = None,
        rql: Optional[str] = None,
        page_size: int = 100,
        max_test_cases: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает список тест-кейсов проекта или по RQL.
        """
        resolved_project_id = project_id or self.default_project_id
        if resolved_project_id is None:
            raise ValueError(
                "project_id не передан и ALLURE_TESTOPS_PROJECT_ID не задан"
            )

        endpoint = "/api/testcase/__search" if rql else "/api/testcase"
        items: List[Dict[str, Any]] = []
        page = 0

        while True:
            params = _compact_params(
                projectId=resolved_project_id,
                rql=rql,
                page=page,
                size=page_size,
                sort=["id,asc"],
            )
            payload = self._request("GET", endpoint, params=params)
            batch = _extract_items(payload)
            if not batch:
                break

            items.extend(batch)
            if max_test_cases is not None and len(items) >= max_test_cases:
                return items[:max_test_cases]

            if len(batch) < page_size:
                break
            page += 1

        return items

    def get_test_case(self, test_case_id: int) -> Dict[str, Any]:
        """Возвращает тест-кейс по ID."""
        payload = self._request("GET", f"/api/testcase/{test_case_id}")
        return payload if isinstance(payload, dict) else {}

    def get_scenario(self, test_case_id: int) -> Dict[str, Any]:
        """Возвращает сценарий тест-кейса."""
        payload = self._request("GET", f"/api/testcase/{test_case_id}/step")
        return payload if isinstance(payload, dict) else {}

    def get_attachments(self, test_case_id: int) -> List[Dict[str, Any]]:
        """Возвращает вложения тест-кейса."""
        payload = self._request(
            "GET",
            "/api/testcase/attachment",
            params=_compact_params(testCaseId=test_case_id, page=0, size=100),
        )
        return _extract_items(payload)

    def get_attachment_content(self, attachment_id: int) -> Dict[str, Any]:
        """Возвращает содержимое вложения и базовую мета-информацию."""
        payload = self._request(
            "GET",
            f"/api/testcase/attachment/{attachment_id}/content",
            return_raw=True,
        )
        return payload if isinstance(payload, dict) else {}

    def get_tags(self, test_case_id: int) -> List[Dict[str, Any]]:
        """Возвращает теги тест-кейса."""
        payload = self._request("GET", f"/api/testcase/{test_case_id}/tag")
        return _extract_items(payload)

    def get_complete_test_case(self, test_case_id: int) -> Dict[str, Any]:
        """
        Получает полный контекст тест-кейса по заданному workflow.
        """
        test_case = self.get_test_case(test_case_id)
        scenario = self.get_scenario(test_case_id)
        attachments = self.get_attachments(test_case_id)

        attachment_contents: List[Dict[str, Any]] = []
        for attachment in attachments:
            attachment_id = attachment.get("id")
            if attachment_id is None:
                continue
            try:
                content = self.get_attachment_content(int(attachment_id))
            except Exception as exc:
                logger.warning(
                    "Не удалось получить content attachment=%s test_case=%s: %s",
                    attachment_id,
                    test_case_id,
                    exc,
                )
                content = {"text": "", "content_type": "", "error": str(exc)}
            attachment_contents.append({**attachment, "content": content})

        tags = self.get_tags(test_case_id)

        return {
            "test_case": test_case,
            "scenario": scenario,
            "attachments": attachment_contents,
            "tags": tags,
        }
