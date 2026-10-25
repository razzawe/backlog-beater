FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/metadata-worker

WORKDIR /app
COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *dependencies])'
RUN groupadd --system app && useradd --system --gid app app
COPY --chown=app:app app ./app
COPY --chown=app:app metadata-worker ./metadata-worker
USER app
CMD ["python", "-m", "celery", "-A", "metadata_worker.worker:app", "worker", "--loglevel=INFO", "--queues=steam_metadata", "--concurrency=1", "--hostname=metadata@%h"]
