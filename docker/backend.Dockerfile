# Backend runtime image. Build context = repo root (project/), because
# requirements/ lives there:
#   docker build -f docker/backend.Dockerfile -t finbot-backend .
# No .env is copied: variables arrive through env_file at runtime.
# scripts/ingest.py is deliberately absent: it runs from the host.
FROM python:3.12.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so this layer is cached across code changes.
# Only backend.txt is installed; the lock constrains it to the resolved versions
# without pulling in the ingest-only packages it also lists.
COPY requirements/base.txt requirements/backend.txt requirements/
COPY requirements.lock.txt .
RUN pip install -r requirements/backend.txt -c requirements.lock.txt

COPY backend/src backend/src

RUN useradd --system --no-create-home finbot
USER finbot

WORKDIR /app/backend
EXPOSE 8000
CMD ["fastapi", "run", "src/main.py", "--host", "0.0.0.0", "--port", "8000"]
