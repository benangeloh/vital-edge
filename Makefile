.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help setup fmt lint typecheck test test-cov schemas schema-check check web-install web-lint up down clean

help: ## Tampilkan bantuan ini
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

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

test: ## Jalankan test Python
	uv run pytest

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

check: lint typecheck test schema-check ## Jalankan semua pemeriksaan (dipakai CI)

up: ## Nyalakan stack dev (Postgres + InfluxDB)
	docker compose up -d

down: ## Matikan stack dev
	docker compose down

clean: ## Hapus artefak build dan cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov
