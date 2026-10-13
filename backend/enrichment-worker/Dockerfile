FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/enrichment-worker:/app/enrichment-service

WORKDIR /app
COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *dependencies])'
RUN groupadd --system app && useradd --system --gid app app
COPY --chown=app:app app ./app
COPY --chown=app:app enrichment-worker ./enrichment-worker
# Reuse lookup/matching/persistence helpers; do not package the FastAPI API.
COPY --chown=app:app enrichment-service/enrichment_service/__init__.py enrichment-service/enrichment_service/hltb.py enrichment-service/enrichment_service/matching.py enrichment-service/enrichment_service/storage.py enrichment-service/enrichment_service/review_cache.py ./enrichment-service/enrichment_service/
USER app
CMD ["python", "-m", "celery", "-A", "enrichment_worker.worker:app", "worker", "--loglevel=INFO", "--queues=enrichment", "--concurrency=1", "--hostname=enrichment@%h"]
