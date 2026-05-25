"""
server.py — FastMCP сервер для семантического поиска по Confluence и Allure TestOps через Qdrant.
"""

from fastmcp import FastMCP

mcp = FastMCP(
    name="qdrant-mcp",
    instructions=(
        "Этот сервер предоставляет семантический поиск по документации Confluence "
        "тест-кейсам Allure TestOps и OpenAPI/Swagger контрактам, проиндексированным "
        "в локальной базе данных Qdrant. Используй явные инструменты "
        "'rag_confluence_*', 'rag_allure_*', 'rag_openapi_*' и 'rag_sync_*'."
    ),
)
