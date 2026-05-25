from qdrant_mcp.openapi_intent import infer_http_method_from_query


def test_infers_post_for_add_barcode_query() -> None:
    assert infer_http_method_from_query("добавление штрихкода barcode sku catalog") == "POST"


def test_ignores_meta_get_words_when_query_asks_for_curl() -> None:
    assert infer_http_method_from_query("получить curl для добавления штрихкода к товару") == "POST"


def test_ignores_meta_search_words_when_query_has_business_action() -> None:
    assert infer_http_method_from_query("найти endpoint удаления штрихкода товара") == "DELETE"


def test_infers_delete_for_delete_query() -> None:
    assert infer_http_method_from_query("удалить штрихкод товара") == "DELETE"


def test_does_not_infer_method_for_mixed_crud_query() -> None:
    assert infer_http_method_from_query("создать и удалить штрихкод товара") is None
