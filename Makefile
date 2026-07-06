.PHONY: install run test bootstrap ingest docker-up docker-down \
        prod-up prod-down prod-logs build

install:
	pip install -r requirements.txt

run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest -q

bootstrap:
	python scripts/bootstrap.py

ingest:
	python scripts/ingest_examples.py

# --- Development (API + GraphDB) ---
docker-up:
	docker compose up --build

docker-down:
	docker compose down

# --- Production (API only, external GraphDB driven by .env) ---
build:
	docker build -t nfmust-catalog-api:latest .

prod-up:
	docker compose -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -f docker-compose.prod.yml down

prod-logs:
	docker compose -f docker-compose.prod.yml logs -f
