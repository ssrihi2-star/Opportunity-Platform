# Deployment

## Local (the supported path)

```bash
cp environment.example .env
# edit .env: SECRET_KEY, SECRET_ENCRYPTION_KEY, POSTGRES_PASSWORD, ADMIN_PASSWORD
docker compose up --build
```

* API: http://localhost:8000/docs
* Web: http://localhost:3000
* Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`

Migrations run automatically in the `api` container entrypoint
(`alembic upgrade head`), followed by the idempotent seed when `SEED_DEMO_DATA=true`.

Generate the two secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"                               # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SECRET_ENCRYPTION_KEY
```

## Before enabling any live source

1. Set `USER_AGENT` and `CONTACT_EMAIL` to real values. SEC EDGAR requires a
   descriptive agent with contact details, and several other publishers will
   throttle an anonymous one.
2. Add the credential where one is required (`fred` -> `api_key`,
   `un_comtrade` -> `subscription_key`, `github` -> `token`, optional but advised).
3. Press **Test connection** on the source card and read the result.
4. Enable it, run it once manually, and read the run history: fetched vs stored vs
   duplicates vs HTTP requests tells you whether it is behaving.

Sources ship **disabled**. Nothing reaches the internet until you turn it on.

## Useful commands

```bash
make up / make down / make logs
make migrate            # alembic upgrade head
make revision m="..."   # autogenerate a migration
make seed               # demo data (idempotent)
make test / make lint / make format
make vectors            # regenerate golden scoring vectors (review the diff)
```

## Production notes

* Terminate TLS at a reverse proxy (Caddy or nginx); set `COOKIE_SECURE=true`.
* Run `api`, `worker` and `beat` as separate services; exactly one `beat`.
* Back up with `pg_dump -Fc` nightly to off-box storage and test a restore
  quarterly. This is documented, not automated.
* Set `AI_DAILY_BUDGET_USD` before enabling any LLM provider.
* Rotating `SECRET_ENCRYPTION_KEY` requires re-encrypting stored credentials;
  the helper script for that is **not implemented** — today the procedure is to
  delete and re-enter each source credential.


## Before trusting Phase 4 output: close the live-data gate

Every opportunity candidate produced by this build is labelled `DEMO`, because
the build environment had no outbound network access and the collectors were
never run against the real APIs.

On a machine with internet access, in this order:

```bash
docker compose up --build -d
docker compose exec api python -m scripts.seed
docker compose exec api python -m scripts.live_validation --dry-run
# add the FRED api_key and (recommended) a GitHub token, then:
docker compose exec api python -m scripts.live_validation
```

`docs/live-validation.md` explains every check, what a failure means, and which
failures are expected on a first run. Until it passes, treat the opportunity
scores as a demonstration that the machinery works — not as findings about the
world.

**Do not adjust a threshold to make live data look better.** A boring live result
is the result.
