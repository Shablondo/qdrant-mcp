import importlib
import os
import sys
import unittest
from unittest.mock import patch


def load_embedder_with_env(env: dict[str, str]):
    with patch.dict(os.environ, env, clear=True):
        sys.modules.pop("qdrant_mcp.embedder", None)
        return importlib.import_module("qdrant_mcp.embedder")


class EmbedderConfigTests(unittest.TestCase):
    def test_public_openai_defaults_are_used(self):
        embedder = load_embedder_with_env(
            {
                "OPENAI_API_KEY": "test-key",
                "EMBED_API_BASE": "https://api.openai.com/v1",
            }
        )

        self.assertEqual(embedder.EMBED_MODEL, "text-embedding-3-large")
        self.assertEqual(embedder.EMBED_DIMENSIONS, 2560)

    def test_openai_api_key_takes_priority(self):
        embedder = load_embedder_with_env(
            {
                "OPENAI_API_KEY": "primary-key",
                "COPILOT_API_KEY": "legacy-key",
                "EMBED_API_BASE": "https://api.openai.com/v1",
            }
        )

        self.assertEqual(embedder.OPENAI_API_KEY, "primary-key")

    def test_legacy_copilot_key_is_still_supported(self):
        embedder = load_embedder_with_env(
            {
                "COPILOT_API_KEY": "legacy-key",
                "EMBED_API_BASE": "https://api.openai.com/v1",
            }
        )

        self.assertEqual(embedder.OPENAI_API_KEY, "legacy-key")


if __name__ == "__main__":
    unittest.main()
