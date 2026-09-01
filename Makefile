.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help dev dev-central dev-ship dev-status dev-reset setup fmt lint typecheck test test-cov schemas schema-check check web-install web-lint up down clean

help: ## Tampilkan bantuan ini
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev: ## Jalankan semuanya: central + kapal simulasi + dashboard
	./scripts/dev.sh

dev-central: ## Jalankan central saja
	./scripts/dev.sh central

dev-ship: ## Jalankan kapal simulasi saja (butuh central hidup)
	./scripts/dev.sh ship

dev-status: ## Apa yang sedang jalan
	./scripts/dev.sh status

dev-reset: ## Hapus data kapal simulasi
	./scripts/dev.sh reset

setup: ## Pasang seluruh dependency (Python + Node)
	uv sync --all-packages
	pnpm install

fmt: ## Format kode Python
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint kode Python
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Type-check kode Python
	uv run mypy .

test: ## Jalankan test Python (integration di-skip bila layanan mati)
	uv run pytest

test-integration: up ## Jalankan integration test terhadap Postgres + InfluxDB sungguhan
	uv run alembic -c central/api/alembic.ini upgrade head
	uv run pytest -m integration

test-e2e: up ## Jalankan simulasi armada end-to-end (3 kapal)
	uv run alembic -c central/api/alembic.ini upgrade head
	uv run pytest -m e2e -v

test-load: ## Jalankan uji beban skala armada
	uv run pytest -m load -v -s

test-security: up ## Jalankan pemeriksaan keamanan
	uv run pytest -m security -v

bench: ## Benchmark 70 kapal x 100 sensor dengan telemetry sintetis
	uv run fleetview-simulate --ships 70 --sensors 100 --ticks 10

migrate: ## Terapkan migrasi database
	uv run alembic -c central/api/alembic.ini upgrade head

migration: ## Buat migrasi baru: make migration M="pesan"
	uv run alembic -c central/api/alembic.ini revision --autogenerate -m "$(M)"

test-cov: ## Jalankan test dengan laporan coverage
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

schemas: ## Hasilkan JSON Schema dari model Pydantic
	uv run python shared/contracts/scripts/export_schemas.py

schema-check: ## Gagal bila JSON Schema hasil generasi sudah basi
	uv run python shared/contracts/scripts/export_schemas.py --check

web-install: ## Pasang dependency frontend
	pnpm install

web-lint: ## Type-check frontend
	pnpm -r --if-present typecheck

web-test: ## Jalankan test frontend
	pnpm -r --if-present test

check: lint typecheck test schema-check web-lint web-test ## Jalankan semua pemeriksaan (dipakai CI)

up: ## Nyalakan stack dev (Postgres + InfluxDB)
	docker compose up -d

down: ## Matikan stack dev
	docker compose down

clean: ## Hapus artefak build dan cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov
