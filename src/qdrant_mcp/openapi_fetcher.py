from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from typing import Any

import httpx


OPENAPI_TIMEOUT = float(os.environ.get("OPENAPI_TIMEOUT", "30"))
OPENAPI_SSL_VERIFY = os.environ.get("OPENAPI_SSL_VERIFY", "true").lower() not in ("false", "0", "no")
OPENAPI_AUTH_HEADER = os.environ.get("OPENAPI_AUTH_HEADER", "")


@dataclass(frozen=True)
class OpenApiFetchResult:
    spec: dict[str, Any]
    spec_hash: str
    fetched_at: str
    etag: str | None = None
    last_modified: str | None = None


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_openapi_spec(source: Any) -> OpenApiFetchResult:
    headers = {"Accept": "application/json"}
    if OPENAPI_AUTH_HEADER:
        name, _, value = OPENAPI_AUTH_HEADER.partition(":")
        if name and value:
            headers[name.strip()] = value.strip()

    with httpx.Client(timeout=OPENAPI_TIMEOUT, verify=OPENAPI_SSL_VERIFY, follow_redirects=True) as client:
        response = client.get(source.api_docs_url, headers=headers)
        response.raise_for_status()
        text = response.text
        payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(f"OpenAPI response is not a JSON object: {source.api_docs_url}")

    return OpenApiFetchResult(
        spec=payload,
        spec_hash=_hash_text(text),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
