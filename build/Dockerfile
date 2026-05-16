FROM python:3.12-slim

WORKDIR /app

# Install the package with all dependencies
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

CMD ["python", "-m", "qdrant_mcp.main"]
