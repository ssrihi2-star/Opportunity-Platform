#!/usr/bin/env bash
# API container entrypoint: wait for the database, migrate, optionally seed, serve.
set -euo pipefail

echo "[entrypoint] waiting for PostgreSQL..."
python - <<'PY'
import os, socket, time, urllib.parse
url = urllib.parse.urlparse(os.environ.get("DATABASE_URL", ""))
host, port = url.hostname or "postgres", url.port or 5432
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] {host}:{port} is accepting connections")
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit(f"[entrypoint] database at {host}:{port} never became reachable")
PY

echo "[entrypoint] running migrations"
alembic upgrade head

if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
  echo "[entrypoint] seeding demo data (idempotent)"
  python -m scripts.seed
fi

echo "[entrypoint] starting API"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
