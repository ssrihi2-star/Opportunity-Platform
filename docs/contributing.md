# Contributing

## Setup without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
export DATABASE_URL=postgresql+asyncpg://ois:ois@localhost:5432/ois
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

## Standards

* Python: `ruff` (lint + format). Type hints everywhere; no bare `except`.
* TypeScript: `eslint`, `prettier`, `tsc --noEmit`.
* Error messages must say what to do next. "Invalid config" is not acceptable;
  "Source 'fred' requires config key 'series'. Set it with PATCH /sources/{id}" is.
* Small functions. Comments only for non-obvious logic — statistics, security,
  and anywhere a reader would reasonably ask "why is it done this way?".
* Every schema change ships with an Alembic migration in the same commit.
* Every scoring change bumps `FORMULA_VERSION` and regenerates the golden vectors.
* New adapters: never import `httpx`; use the injected `Fetcher`. Always add a
  fixture file and tests. Declare `documented_rate_limit` truthfully.
* No secrets in code, fixtures or tests.
* Placeholder implementations must raise `NotImplementedError` and be listed in
  `docs/changelog.md`. Silent stubs are not acceptable.

## Security issues

Email the maintainer privately. Do not open a public issue.
