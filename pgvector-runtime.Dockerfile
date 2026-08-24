FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pull the upstream PGVector runtime so this remains aligned with the project it targets.
RUN git clone --depth 1 https://github.com/BerriAI/litellm-pgvector.git /tmp/litellm-pgvector \
    && cp -a /tmp/litellm-pgvector/. /app/ \
    && rm -rf /tmp/litellm-pgvector

RUN pip install --no-cache-dir -r requirements.txt \
    && prisma generate

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
