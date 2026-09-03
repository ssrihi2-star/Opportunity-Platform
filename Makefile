.PHONY: help up down logs build migrate revision seed test test-fast lint format shell psql clean vectors

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:            ## Build and start the whole stack
	docker compose up --build

down:          ## Stop the stack
	docker compose down

logs:          ## Tail all logs
	docker compose logs -f

migrate:       ## Apply database migrations
	docker compose run --rm api alembic upgrade head

revision:      ## Create a migration: make revision m="add widgets"
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

seed:          ## Load demo data (idempotent)
	docker compose run --rm api python -m scripts.seed

test:          ## Run the backend test suite
	cd backend && ENV=test python -m pytest -q

test-fast:     ## Unit tests only (no API round-trips)
	cd backend && ENV=test python -m pytest -q -k "not api"

vectors:       ## Regenerate the golden scoring vectors (review the diff!)
	cd backend && ENV=test python -m scripts.generate_scoring_vectors

lint:          ## Lint backend and frontend
	cd backend && ruff check app tests scripts
	cd frontend && npm run lint

format:        ## Format backend
	cd backend && ruff format app tests scripts

shell:         ## Shell in the API container
	docker compose exec api bash

psql:          ## psql into the database
	docker compose exec postgres psql -U $${POSTGRES_USER:-ois} -d $${POSTGRES_DB:-ois}

clean:         ## Remove volumes (DESTROYS DATA)
	docker compose down -v
