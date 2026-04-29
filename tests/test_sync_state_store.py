import threading
from types import SimpleNamespace

import pytest

from sync_state_store import SYNC_STATE_COLLECTION, _ensure_collection


class RaceyCollectionClient:
    def __init__(self) -> None:
        self.created = False
        self.create_calls = 0

    def get_collections(self):
        collections = [SimpleNamespace(name=SYNC_STATE_COLLECTION)] if self.created else []
        return SimpleNamespace(collections=collections)

    def create_collection(self, **kwargs):
        self.create_calls += 1
        if self.create_calls == 1:
            threading.Event().wait(0.05)
            self.created = True
            return True
        raise RuntimeError(f"Wrong input: Collection `{SYNC_STATE_COLLECTION}` already exists!")


def test_ensure_collection_is_safe_for_concurrent_first_use() -> None:
    client = RaceyCollectionClient()
    errors = []

    def ensure() -> None:
        try:
            _ensure_collection(client)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=ensure)
    second = threading.Thread(target=ensure)
    first.start()
    second.start()
    first.join()
    second.join()

    assert errors == []
    assert client.create_calls == 1
