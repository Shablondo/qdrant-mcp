import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_CHUNKS = 256


def get_flush_chunks(
    env_name: str = "RAG_SYNC_FLUSH_CHUNKS",
    default: int = DEFAULT_FLUSH_CHUNKS,
) -> int:
    try:
        value = int(os.environ.get(env_name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default
