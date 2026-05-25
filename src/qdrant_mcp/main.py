"""
main.py — точка входа для qdrant-mcp MCP сервера.
Сервер предоставляет семантический поиск по документации Confluence,
тест-кейсам Allure TestOps и OpenAPI/Swagger контрактам через Qdrant.
"""

import logging

from qdrant_mcp.server import mcp

# Импорт tool-модулей регистрирует @mcp.tool функции
import qdrant_mcp.confluence_tools  # noqa: F401
import qdrant_mcp.allure_tools      # noqa: F401
import qdrant_mcp.openapi_tools     # noqa: F401
import qdrant_mcp.sync_tools        # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Entry point for qdrant-mcp MCP server (used by uvx and console_scripts)."""
    logger.info("Запуск qdrant-mcp сервера...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
