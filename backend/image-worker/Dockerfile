FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/image-worker
WORKDIR /app
COPY pyproject.toml ./
COPY image-worker/requirements.txt ./image-requirements.txt
RUN python -c 'import subprocess, sys, tomllib; deps=tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable,"-m","pip","install","--no-cache-dir",*deps,"-r","image-requirements.txt"])'
RUN groupadd --system app && useradd --system --gid app --create-home app
COPY --chown=app:app app ./app
COPY --chown=app:app image-worker ./image-worker
USER app
CMD ["python", "-m", "celery", "-A", "image_worker.worker:app", "worker", "--loglevel=INFO", "--queues=images", "--concurrency=1", "--hostname=images@%h"]
