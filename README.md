# qdrant-mcp

MCP сервер для семантического поиска по документации Confluence и тест-кейсам Allure TestOps через локальный Qdrant.

## Что делает

- **Индексирует** страницы Confluence (страница + все дочерние рекурсивно) в локальный Qdrant
- **Индексирует** тест-кейсы Allure TestOps с полным hydration workflow:
  `test case -> scenario -> attachments -> tags`
- **Векторизует** контент через корпоративную модель `copilot-embed-4b` (dim=2560)
- **Предоставляет** семантический поиск по документации и базе тест-кейсов из любого режима Kilo Code

## Требования

- Docker
- Локальный Qdrant (запущен на `localhost:6333`)
- Доступ к корпоративному Embeddings API (OpenAI-compatible)
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
- `COPILOT_API_KEY`
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

### 2. Собери Docker образ

```bash
cd qdrant-mcp
docker build -t qdrant-mcp:latest .
```

### 3. Добавь в .kilocode/mcp.json

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
        "-e", "COPILOT_API_KEY",
        "-e", "EMBED_API_ENDPOINT",
        "-e", "EMBED_DIMENSIONS",
        "-e", "QDRANT_URL",
        "qdrant-mcp:latest"
    ],
    "env": {
        "ALLURE_TESTOPS_URL": "https://your-allure-testops.com",
        "ALLURE_TESTOPS_API_TOKEN": "***",
        "ALLURE_TESTOPS_PROJECT_ID": "123",
        "CONFLUENCE_URL": "https://your-confluence.example.com",
        "CONFLUENCE_PERSONAL_TOKEN": "***",
        "CONFLUENCE_SSL_VERIFY": "false",
        "EMBED_MODEL": "copilot-embed-4b",
        "COPILOT_API_KEY": "***",
        "EMBED_API_ENDPOINT": "https://your-copilot-endpoint.example.com/v1/embeddings",
        "EMBED_DIMENSIONS": "2560",
        "QDRANT_URL": "http://host.docker.internal:6333"
    },
    "disabled": false,
    "alwaysAllow": [
        "search",
        "search_hybrid_tool",
        "find_similar_pages",
        "search_by_examples",
        "get_indexed_page",
        "list_indexed",
        "get_collection_info",
        "search_allure_test_cases",
        "get_indexed_allure_test_case",
        "list_indexed_allure_test_cases",
        "get_allure_collection_info"
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
        "qdrant-mcp:latest"
    ],
    "disabled": false,
    "alwaysAllow": [
        "search",
        "search_hybrid_tool",
        "find_similar_pages",
        "search_by_examples",
        "get_indexed_page",
        "list_indexed",
        "get_collection_info",
        "search_allure_test_cases",
        "get_indexed_allure_test_case",
        "list_indexed_allure_test_cases",
        "get_allure_collection_info"
    ]
}
```

## Использование

### Первичная индексация

В любом режиме Kilo Code вызови инструмент `index_page_tree`:

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
owner="Nikita.Shablinsky"

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

Инструмент `search_hybrid_tool` сохранён для совместимости, но сейчас использует тот же dense-поиск, что и основной `search`.

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
| `index_page_tree(page_id)` | Индексирует страницу и все дочерние рекурсивно |
| `reindex_page_tree(page_id)` | Переиндексирует дерево (удалить + переиндексировать) |
| `search(query, limit?, root_page_id?, space_key?, group_by?, group_size?, exclude_page_ids?, search_vector?, last_modified_after?, last_modified_before?, title_filter?, context_size?)` | Семантический поиск с расширенными фильтрами, multi-vector и расширением контекста |
| `search_hybrid_tool(query, limit?, root_page_id?, space_key?, exclude_page_ids?, search_vector?, last_modified_after?, last_modified_before?, title_filter?)` | Dense-only поиск через совместимый интерфейс |
| `find_similar_pages(page_id, limit?, space_key?, root_page_id?, exclude_page_ids?, search_vector?)` | Поиск похожих страниц (Recommendation API) |
| `search_by_examples(positive_page_ids, negative_page_ids?, limit?, space_key?, root_page_id?, exclude_page_ids?, search_vector?)` | Поиск по примерам (Discovery API) |
| `get_indexed_page(page_id)` | Получить все чанки страницы из Qdrant |
| `list_indexed()` | Список всех проиндексированных страниц |
| `get_collection_info()` | Статистика коллекции Qdrant |
| `index_allure_test_cases(project_id?, rql?, page_size?, max_test_cases?)` | Индексирует тест-кейсы Allure TestOps |
| `reindex_allure_test_cases(project_id?, rql?, page_size?, max_test_cases?)` | Переиндексирует тест-кейсы Allure TestOps |
| `search_allure_test_cases(query, limit?, project_id?, status?, owner?, tags?, chunk_types?, exclude_test_case_ids?, group_by?, group_size?, search_vector?, updated_after?, updated_before?, name_filter?)` | Семантический поиск по тест-кейсам Allure |
| `get_indexed_allure_test_case(test_case_id)` | Получить все чанки тест-кейса из Allure-индекса |
| `list_indexed_allure_test_cases()` | Список всех проиндексированных тест-кейсов |
| `get_allure_collection_info()` | Статистика коллекции тест-кейсов Allure |

## Структура данных в Qdrant

**Коллекция:** `confluence_docs`
**Размерность:** 2560
**Метрика:** Cosine
**Named Vectors:** `content` (контент чанка), `title` (заголовок страницы)
**Payload каждого Point:**
```json
{
  "page_id": "1392589758",
  "title": "7.25 Fulfillment Сервис фулфилмента",
  "url": "https://wiki.x5.ru/spaces/OMNI/pages/...",
  "space_key": "OMNI",
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
**Размерность:** 2560
**Метрика:** Cosine
**Named Vectors:** `content` (контент чанка), `title` (название тест-кейса)

**Payload каждого Point:**
```json
{
  "test_case_id": "12345",
  "project_id": "38",
  "name": "Проверка валидации формы логина",
  "status": "Active",
  "owner": "Nikita.Shablinsky",
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
├── Dockerfile
├── .dockerignore
├── .gitignore
├── requirements.txt
├── .env.example
├── README.md
└── src/
    ├── allure_client.py        # Клиент к Allure TestOps API
    ├── allure_indexer.py       # Индексация тест-кейсов Allure
    ├── allure_qdrant_store.py  # Хранение и поиск тест-кейсов Allure в Qdrant
    ├── embedder.py             # Клиент к OpenAI-compatible Embeddings API
    ├── indexer.py              # Рекурсивный обход Confluence + запись в Qdrant
    ├── main.py                 # FastMCP сервер, регистрация инструментов
    ├── qdrant_store.py         # Обёртка над qdrant-client SDK
    └── tool_utils.py           # Общие утилиты инструментов и форматирования
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
- Инструмент `search_hybrid_tool` сохранён для обратной совместимости
- В текущей версии использует dense-only поиск без внешних sparse-моделей

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
