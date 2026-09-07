FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev,vector]"

COPY . .

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000
# The worker and beat services build from this same image but run no HTTP
# server, so an image-level HTTP probe would mark them unhealthy forever.
# Each service declares its own healthcheck in docker-compose.yml instead.
HEALTHCHECK NONE

CMD ["./entrypoint.sh"]
