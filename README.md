# qdrant-mcp

MCP сервер для семантического поиска по документации Confluence и тест-кейсам Allure TestOps через локальный Qdrant.

## Что делает

- **Индексирует** страницы Confluence (страница + все дочерние рекурсивно) в локальный Qdrant
- **Индексирует** тест-кейсы Allure TestOps с полным hydration workflow:
  `test case -> scenario -> attachments -> tags`
- **Векторизует** контент через публичную модель `text-embedding-3-large` (dim=3072)
- **Предоставляет** семантический поиск по документации и базе тест-кейсов из любого режима Kilo Code

## Быстрый старт (uvx)

```bash
uvx --from git+https://github.com/shablondo/qdrant-mcp-github qdrant-mcp
```

uvx автоматически установит все зависимости и запустит MCP сервер без Docker.

### Конфигурация opencode.json для uvx

```json
"qdrant-mcp": {
    "type": "local",
    "command": ["uvx", "--from", "git+https://github.com/shablondo/qdrant-mcp-github", "qdrant-mcp"],
    "env": {
        "QDRANT_URL": "http://localhost:6333",
        "RAG_SOURCES_PATH": "/Users/shablondo/VcCodeProject/generate-test-case-opencode/config/rag_sources.yaml",
        "ALLURE_TESTOPS_URL": "...",
        "ALLURE_TESTOPS_API_TOKEN": "...",
        "ALLURE_TESTOPS_PROJECT_ID": "...",
        "CONFLUENCE_URL": "...",
        "CONFLUENCE_PERSONAL_TOKEN": "...",
        "CONFLUENCE_SSL_VERIFY": "false",
        "EMBED_MODEL": "text-embedding-3-large",
        "OPENAI_API_KEY": "...",
        "EMBED_API_ENDPOINT": "https://api.openai.com/v1/embeddings",
        "EMBED_DIMENSIONS": "3072"
    },
    "enabled": true,
    "timeout": 3600000
}
```

Обрати внимание:
- `QDRANT_URL` использует `localhost`, а не `host.docker.internal` (запуск нативный, не через Docker)
- `RAG_SOURCES_PATH` указывает путь к `rag_sources.yaml` на хост-машине
- `--add-host` и `-v` volume mount больше не нужны

## Требования

- **uv** (рекомендуется) — для запуска через `uvx`
- Docker (альтернативно) — для запуска через контейнер
- Локальный Qdrant (запущен на `localhost:6333`)
- Доступ к OpenAI API или совместимому Embeddings API
- Доступ к Confluence
- Доступ к Allure TestOps

## Установка

### 1. Настрой переменные окружения

Сервер читает настройки напрямую из переменных окружения. Файл `.env` не обязателен: его можно использовать как локальный шаблон или передавать значения в контейнер напрямую через MCP конфиг.

Обязательные переменные:
- `ALLURE_TESTOPS_URL`
- `ALLURE_TESTOPS_API_TOKEN`
- `ALLURE_TESTOPS_PROJECT_ID`
- `CONFLUENCE_URL`
- `CONFLUENCE_PERSONAL_TOKEN`
- `CONFLUENCE_SSL_VERIFY`
- `EMBED_MODEL`
- `OPENAI_API_KEY`
- `EMBED_API_ENDPOINT`
- `EMBED_DIMENSIONS`
- `QDRANT_URL`

Если хочешь использовать локальный файл, можешь взять шаблон:

```bash
cp .env.example .env
```

Опционально:
- `ALLURE_QDRANT_COLLECTION` — имя коллекции для тест-кейсов (`allure_test_cases` по умолчанию)
- `ALLURE_TESTOPS_TIMEOUT` — timeout запросов к Allure TestOps
- `ALLURE_TESTOPS_SSL_VERIFY` — проверка SSL сертификата (`true`/`false`)
- `ALLURE_ATTACHMENT_MAX_CHARS` — сколько символов текстового вложения сохранять в индекс
- `OPENAPI_TIMEOUT` — timeout запросов к Swagger/OpenAPI
- `OPENAPI_SSL_VERIFY` — проверка SSL сертификата Swagger/OpenAPI (`true`/`false`); для внутренних stage URL обычно нужно `false`

### 2. Запуск через uvx (рекомендуется)

```bash
uvx --from git+https://github.com/shablondo/qdrant-mcp-github qdrant-mcp
```

При использовании `uvx` переменные окружения передаются через блок `env` в конфигурации MCP (см. пример выше).

### 3. Собери Docker образ

```bash
cd qdrant-mcp
docker build -f build/Dockerfile -t qdrant-mcp:latest .
```

### 4. Docker образ в GHCR

Образ публикуется в GitHub Container Registry после push тега формата `vX.Y.Z`.

Актуальный адрес образа:

```bash
ghcr.io/shablondo/qdrant-mcp:latest
```

Пример локального запуска:

```bash
docker run --rm -i \
  --add-host host.docker.internal:host-gateway \
  -e ALLURE_TESTOPS_URL \
  -e ALLURE_TESTOPS_API_TOKEN \
  -e ALLURE_TESTOPS_PROJECT_ID \
  -e CONFLUENCE_URL \
  -e CONFLUENCE_PERSONAL_TOKEN \
  -e CONFLUENCE_SSL_VERIFY \
  -e OPENAPI_TIMEOUT \
  -e OPENAPI_SSL_VERIFY \
  -e EMBED_MODEL \
  -e OPENAI_API_KEY \
  -e EMBED_API_ENDPOINT \
  -e EMBED_DIMENSIONS \
  -e QDRANT_URL \
  ghcr.io/shablondo/qdrant-mcp:latest
```

Для MCP-конфига вместо локальной сборки можно использовать этот образ напрямую.

## CI/CD

При пуше в `master`:
1. **Tests** — запускаются все тесты через `uv run pytest -v`
2. **Auto-tag** — автоматически поднимается patch-версия (создаётся тег `vX.Y.Z`)
3. **Docker build & push** — собирается multi-arch образ (amd64 + arm64) и пушится в GHCR

Ручной запуск: workflow dispatch через GitHub Actions UI.

## Ручная проверка актуальности RAG

Source registry задаётся в `config/rag_sources.yaml`. У каждого source есть `sync_interval_minutes`; стандартный интервал актуальности - 1440 минут, то есть один раз в сутки. `rag_sync_sources` проверяет `next_due_at` и синхронизирует только те sources, у которых истёк интервал актуальности. Если source ещё актуален, он будет пропущен со статусом `skipped_not_due`.

Синхронизация выполняется параллельно по независимым sources. Один и тот же source защищён in-process lock по ключу `kind:id`; если такой source уже синхронизируется, повторный запуск вернёт `skipped_locked` и не возьмёт его в работу повторно.

Параллельность управляется переменными окружения:

- `RAG_SYNC_MAX_WORKERS` - сколько sources синхронизировать одновременно, по умолчанию `4`.
- `RAG_ALLURE_SYNC_MAX_WORKERS` - сколько Allure test cases внутри одного Allure source обрабатывать одновременно, по умолчанию `6`.
- `RAG_SYNC_FLUSH_CHUNKS` - порог числа чанков, после которого накопленные данные отправляются на эмбеддинг и в Qdrant (стриминговый пайплайн). По умолчанию `256`. Уменьшение снижает пиковое потребление памяти, но может увеличить число HTTP-запросов к embedder API.

Синхронизация использует стриминговый пайплайн: чанки накапливаются порциями и флашатся (embed → upsert → save_sync_states) по достижении `RAG_SYNC_FLUSH_CHUNKS`. Если одна пачка эмбеддера падает (`EmbedResponseError`), затронутые страницы/тест-кейсы/операции помечаются как `errors`, **НЕ** сохраняют `sync_state` (и будут перепроверены при следующем запуске), а обработка продолжается со следующей пачкой. Источник возвращается со статусом `completed_with_errors` (ненулевое `errors`), а не `error`.

Запуск ручной проверки:

```bash
python -m rag_sync_cli once --sources-path config/rag_sources.yaml
```

Принудительная проверка без ожидания интервала:

```bash
python -m rag_sync_cli once --sources-path config/rag_sources.yaml --force
```

`--force` и MCP-параметр `force=true` игнорируют `sync_interval_minutes` и запускают проверку сразу.

Проверка per-source статуса:

```bash
python -m rag_sync_cli source-status --sources-path config/rag_sources.yaml
```

Через MCP доступны те же данные:

- `rag_sync_sources(force=false)`
- `rag_get_source_sync_status()`
- `rag_get_sync_status(kind="rag_source")`

`rag_get_sync_status` предназначен для точечной диагностики sync state. Без `kind` или `source_id_prefix` он не выгружает коллекцию, чтобы не забивать контекст LLM; для подробностей передавайте фильтр, например `kind="openapi_operation", source_id_prefix="service-name-pp-test:"`.

### 5. Добавь MCP конфигурацию

**Рекомендуется:** использовать `uvx` (см. [Быстрый старт](#быстрый-старт-uvx)).  
**Альтернатива:** Docker (см. примеры ниже).

Примеры для `.kilocode/mcp.json` (также подходят для opencode.json с адаптацией формата):

Вариант A. Передавать значения напрямую в конфиг KiloCode:

```json
"qdrant-mcp": {
    "command": "docker",
    "args": [
        "run", "--rm", "-i",
        "--name", "qdrant-mcp",
        "--add-host", "host.docker.internal:host-gateway",
        "-e", "ALLURE_TESTOPS_URL",
        "-e", "ALLURE_TESTOPS_API_TOKEN",
        "-e", "ALLURE_TESTOPS_PROJECT_ID",
        "-e", "CONFLUENCE_URL",
        "-e", "CONFLUENCE_PERSONAL_TOKEN",
        "-e", "CONFLUENCE_SSL_VERIFY",
        "-e", "EMBED_MODEL",
        "-e", "OPENAI_API_KEY",
        "-e", "EMBED_API_ENDPOINT",
        "-e", "EMBED_DIMENSIONS",
        "-e", "QDRANT_URL",
        "ghcr.io/shablondo/qdrant-mcp:latest"
    ],
    "env": {
        "ALLURE_TESTOPS_URL": "https://your-allure-testops.com",
        "ALLURE_TESTOPS_API_TOKEN": "***",
        "ALLURE_TESTOPS_PROJECT_ID": "123",
        "CONFLUENCE_URL": "https://your-confluence.example.com",
        "CONFLUENCE_PERSONAL_TOKEN": "***",
        "CONFLUENCE_SSL_VERIFY": "false",
        "EMBED_MODEL": "text-embedding-3-large",
        "OPENAI_API_KEY": "***",
        "EMBED_API_ENDPOINT": "https://api.openai.com/v1/embeddings",
        "EMBED_DIMENSIONS": "3072",
        "QDRANT_URL": "http://host.docker.internal:6333"
    },
    "disabled": false,
    "alwaysAllow": [
        "rag_sync_sources",
        "rag_get_sync_status",
        "rag_get_source_sync_status",
        "rag_list_sources",
        "rag_confluence_search",
        "rag_confluence_get_indexed_page",
        "rag_allure_search_test_cases",
        "rag_allure_get_indexed_test_case",
        "rag_openapi_find_curl",
        "rag_openapi_search_operations"
    ]
}
```

Вариант B. Использовать `--env-file` с локальным `.env`:

```json
"qdrant-mcp": {
    "command": "docker",
    "args": [
        "run", "--rm", "-i",
        "--name", "qdrant-mcp",
        "--add-host", "host.docker.internal:host-gateway",
        "--env-file", "/полный/путь/к/qdrant-mcp/.env",
        "ghcr.io/shablondo/qdrant-mcp:latest"
    ],
    "disabled": false,
    "alwaysAllow": [
        "rag_sync_sources",
        "rag_get_sync_status",
        "rag_get_source_sync_status",
        "rag_list_sources",
        "rag_confluence_search",
        "rag_confluence_get_indexed_page",
        "rag_allure_search_test_cases",
        "rag_allure_get_indexed_test_case",
        "rag_openapi_find_curl",
        "rag_openapi_search_operations"
    ]
}
```

## Использование

### Первичная индексация

В любом режиме Kilo Code вызови инструмент `rag_confluence_index_page_tree`:

```
Проиндексируй страницу 1392589758 и все её дочерние страницы
```

Сервер рекурсивно обойдёт всё дерево и сохранит в Qdrant.

### Семантический поиск

```
Найди в документации информацию о требованиях к фулфилменту
```

### Индексация тест-кейсов Allure TestOps

Индексируется полный контекст тест-кейса по workflow:
1. `allure_getTestCase(id)`
2. `allure_getScenario(id)`
3. `allure_getAttachments(testCaseId)`
4. `allure_getAttachmentContent(id)` для каждого вложения
5. `allure_getTags(testCaseId)`

#### Первичная индексация проекта

```
Проиндексируй тест-кейсы Allure из проекта 38
```

#### Индексация подмножества через RQL

```
Проиндексируй тест-кейсы Allure из проекта 38 по запросу "tag = 'smoke'"
```

**Правила форматирования RQL запросов:**

При использовании RQL (Request Query Language) для фильтрации тест-кейсов важно правильно форматировать значения в зависимости от их типа:

- **Строковые значения** — заключать в кавычки и экранировать:
  - `tag="fulfillment"`
  - `status="Active"`
  - `owner="Nikita.Shablinsky"`

- **Числовые значения** — указывать без кавычек:
  - `id=12345`
  - `projectId=38`

**Примеры RQL запросов:**

```
# Поиск по тегу
tag="smoke"

# Поиск по статусу
status="Active"

# Поиск по владельцу
owner="user.name"

# Комбинированный запрос
tag="smoke" and status="Active"

# Поиск по ID тест-кейса
id=12345

# Поиск по проекту
projectId=38
```

#### Переиндексация

```
Переиндексируй все тест-кейсы Allure из проекта 38
```

#### Семантический поиск по тест-кейсам

```
Найди в тест-кейсах Allure референсы для проверки валидации формы логина
```

#### Поиск с фильтрами

```
Найди тест-кейсы Allure по checkout, только с тегами smoke и regression
```

#### Получение полного проиндексированного кейса

```
Покажи проиндексированный тест-кейс Allure 12345
```

#### Поиск по заголовкам

Для поиска по заголовкам страниц:

```
Найди страницы с заголовками, содержащими "фулфилмент", ищи по заголовкам
```

#### Группировка результатов по странице

Чтобы избежать дубликатов чанков одной страницы:

```
Найди информацию о фулфилменте, сгруппируй результаты по странице
```

#### Исключение страниц из результатов

```
Найди информацию о фулфилменте, исключи страницы 123456 и 789012
```

#### Фильтрация по дате изменения

```
Найди информацию о фулфилменте, изменённую после 2024-01-01
```

```
Найди документы, изменённые между 2024-01-01 и 2024-12-31
```

#### Текстовый поиск по заголовкам

```
Найди страницы с заголовком, содержащим "API"
```

#### Расширение контекста

```
Найди информацию о фулфилменте, покажи 2 соседних чанка для контекста
```

### Расширенный поиск

```
Найди информацию о фулфилменте, используй гибридный поиск
```

Инструмент `rag_confluence_search_hybrid` сейчас использует тот же dense-поиск, что и основной `rag_confluence_search`.

### Поиск похожих страниц

```
Найди страницы, похожие на страницу 1392589758
```

Это полезно для навигации по связанной документации.

#### Поиск похожих по заголовкам

```
Найди страницы с похожими заголовками на страницу 1392589758, ищи по заголовкам
```

### Поиск по примерам (Discovery API)

Используйте positive и negative примеры для уточнения поиска:

```
Найди документы похожие на страницы 1392589758 и 1392589759, но не похожие на 1392589760
```

Это полезно для:
- Уточнения запроса через примеры
- Поиска документов в определённом стиле
- Исключения нерелевантных тем

### Переиндексация после изменений

```
Переиндексируй раздел документации 1392589758
```


## MCP инструменты

| Инструмент | Описание |
|---|---|
| `rag_confluence_index_page_tree(page_id)` | Индексирует страницу и все дочерние рекурсивно |
| `rag_confluence_reindex_page_tree(page_id)` | Переиндексирует дерево Confluence |
| `rag_confluence_search(query, ...)` | Семантический поиск по Confluence |
| `rag_confluence_search_hybrid(query, ...)` | Dense-only поиск через совместимый интерфейс |
| `rag_confluence_find_similar_pages(page_id, ...)` | Поиск похожих страниц |
| `rag_confluence_search_by_examples(positive_page_ids, ...)` | Поиск по примерам |
| `rag_confluence_get_indexed_page(page_id)` | Получить чанки страницы |
| `rag_confluence_list_indexed_pages()` | Список проиндексированных страниц |
| `rag_confluence_get_collection_info()` | Статистика коллекции Confluence |
| `rag_allure_index_test_cases(project_id?, rql?, page_size?, max_test_cases?)` | Индексирует тест-кейсы Allure TestOps |
| `rag_allure_reindex_test_cases(project_id?, rql?, page_size?, max_test_cases?)` | Переиндексирует тест-кейсы Allure TestOps |
| `rag_allure_search_test_cases(query, ...)` | Семантический поиск по тест-кейсам Allure |
| `rag_allure_get_indexed_test_case(test_case_id)` | Получить чанки тест-кейса |
| `rag_allure_list_indexed_test_cases()` | Список проиндексированных тест-кейсов |
| `rag_allure_get_collection_info()` | Статистика коллекции Allure |
| `rag_openapi_search_operations(query, ...)` | Compact-поиск OpenAPI operations |
| `rag_openapi_find_curl(query, ...)` | Найти operation и вернуть curl |
| `rag_openapi_get_operation(service, method, path)` | Получить полный OpenAPI contract |
| `rag_sync_sources(...)` | Ручная синхронизация sources с проверкой актуальности |
| `rag_get_source_sync_status()` | Per-source freshness status |
| `rag_get_sync_status(kind?, source_id_prefix?, limit?)` | Ограниченная диагностика sync state, требует фильтр |

## Структура данных в Qdrant

**Коллекция:** `confluence_docs`
**Размерность:** 3072
**Метрика:** Cosine
**Named Vectors:** `content` (контент чанка), `title` (заголовок страницы)
**Payload каждого Point:**
```json
{
  "page_id": "1392589758",
  "title": "7.25 Test Сервис Test",
  "url": "https://your-confluence.example.com/spaces/TEAM/pages/...",
  "space_key": "TEAM",
  "root_page_id": "1392589758",
  "chunk_index": 0,
  "text": "текст фрагмента",
  "last_modified": "2024-01-15T10:00:00Z"
}
```

**Named Vectors:**
- `content`: векторизация текста чанка
- `title`: векторизация заголовка страницы (один вектор для всех чанков страницы)

### Коллекция тест-кейсов Allure

**Коллекция:** `allure_test_cases`
**Размерность:** 3072
**Метрика:** Cosine
**Named Vectors:** `content` (контент чанка), `title` (название тест-кейса)

**Payload каждого Point:**
```json
{
  "test_case_id": "12345",
  "project_id": "38",
  "name": "Проверка валидации формы логина",
  "status": "Active",
  "owner": "User.Name",
  "updated_at": "2025-01-15T10:00:00Z",
  "tags": ["smoke", "regression"],
  "chunk_type": "scenario",
  "chunk_index": 2,
  "text": "текст фрагмента тест-кейса",
  "source": "allure"
}
```

**Типы чанков:**
- `overview`
- `scenario`
- `tags`
- `attachment`

**Payload индексы:**
- `page_id` (KEYWORD) - быстрый поиск по ID страницы
- `root_page_id` (KEYWORD) - фильтрация по дереву страниц
- `space_key` (KEYWORD) - фильтрация по пространству Confluence
- `last_modified` (DATETIME) - фильтрация по дате изменения
- `title` (TEXT) - текстовый поиск по заголовкам

## Структура проекта

```
qdrant-mcp/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── .dockerignore
├── .github/
│   └── workflows/
│       └── docker-publish.yml
├── build/
│   └── Dockerfile
├── tests/
└── src/
    └── qdrant_mcp/
        ├── __init__.py
        ├── main.py                   # FastMCP сервер, регистрация инструментов
        ├── allure_client.py          # Клиент к Allure TestOps API
        ├── allure_indexer.py         # Индексация тест-кейсов Allure
        ├── allure_qdrant_store.py    # Хранение и поиск тест-кейсов Allure в Qdrant
        ├── allure_sync.py            # Синхронизация Allure sources
        ├── confluence_sync.py        # Синхронизация Confluence sources
        ├── embedder.py               # Клиент к OpenAI / OpenAI-compatible Embeddings API
        ├── indexer.py                # Рекурсивный обход Confluence + запись в Qdrant
        ├── openapi_curl.py           # Генерация curl-шаблонов
        ├── openapi_fetcher.py        # Загрузка OpenAPI спецификаций
        ├── openapi_indexer.py        # Индексация OpenAPI операций
        ├── openapi_intent.py         # Извлечение HTTP методов из запроса
        ├── openapi_parser.py         # Парсинг OpenAPI спецификаций
        ├── openapi_qdrant_store.py   # Хранение OpenAPI операций в Qdrant
        ├── qdrant_store.py           # Обёртка над qdrant-client SDK
        ├── rag_sources.py            # Модели RAG sources
        ├── rag_sync.py               # Оркестратор синхронизации
        ├── rag_sync_cli.py           # CLI для ручной синхронизации
        ├── sync_state_store.py       # Хранение состояния синхронизации
        └── tool_utils.py             # Общие утилиты инструментов и форматирования
```

## Безопасность

- **НИКОГДА** не пушьте `.env` в git — добавьте его в `.gitignore`
- Используй `.env.example` как шаблон, если выбираешь вариант с локальным `.env`
- `.dockerignore` исключает локальные секреты и служебные файлы из Docker build context

## Новые возможности (v3.0)

### Payload Indexing Expansion
- Индексы для `last_modified` (DATETIME) - быстрая фильтрация по дате изменения
- Индексы для `title` (TEXT) - текстовый поиск по заголовкам
- Ускорение всех фильтров по этим полям

### Search Compatibility
- Legacy MCP tool names удалены из публичного registry
- Используйте явные `rag_confluence_*`, `rag_allure_*`, `rag_openapi_*`, `rag_sync_*`

### Search Context Expansion
- Получение соседних чанков для большего контекста
- Параметр `context_size` для управления объёмом контекста
- Лучшее понимание найденного фрагмента

### Улучшенная фильтрация
- Фильтрация по дате изменения (`last_modified_after`, `last_modified_before`)
- Текстовый поиск по заголовкам (`title_filter`)
- Комбинирование всех фильтров

## Новые возможности (v2.0)

### Multi-Vector Support
- Отдельные векторы для заголовков и контента
- Поиск по заголовкам (`search_vector="title"`) или по контенту (`search_vector="content"`)
- Более точный поиск по заголовкам страниц

### Discovery API
- Поиск по примерам через positive/negative примеры
- Уточнение запроса через релевантные документы
- Исключение нерелевантных тем

### Улучшенная фильтрация
- Группировка результатов по странице (`group_by="page_id"`)
- Исключение страниц из результатов (`exclude_page_ids`)
- Комбинированные фильтры по пространствам и деревьям страниц

### Recommendation API
- Поиск похожих страниц на основе векторов
- Поддержка фильтров и исключений
- Выбор вектора для поиска (content/title)
