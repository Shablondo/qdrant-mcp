from __future__ import annotations

import functools
import os

from qdrant_client import QdrantClient


QDRANT_URL = os.environ.get("QDRANT_URL", "http://host.docker.internal:6333")


@functools.lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)
