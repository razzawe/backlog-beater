FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Cache dependency installation independently of application source changes.
COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; dependencies = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *dependencies])'

RUN groupadd --system app && useradd --system --gid app app

COPY --chown=app:app app ./app
COPY --chown=app:app auth_service ./auth_service

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "auth_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
