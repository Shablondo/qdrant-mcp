FROM docker-registry.x5.ru/python:3.11.15-trixie

ENV PIP_INDEX_URL=https://art.x5.ru/artifactory/pypi-global-remote
ENV PIP_EXTRA_INDEX_URL=https://art.x5.ru/artifactory/pypi-pytorch-remote
ENV PIP_TRUSTED_HOST=art.x5.ru

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY src/ ./src/

# Запуск MCP сервера через stdin/stdout
CMD ["python", "src/main.py"]
