.PHONY: install test lint build dev-api dev-worker dev-web

install:
	cd backend && uv sync
	cd frontend && npm ci

test:
	cd backend && uv run pytest -q
	cd frontend && npm run -s test -- --run

lint:
	cd backend && uv run ruff check src

build:
	cd frontend && npm run -s build

dev-api:
	@if [ -f backend/src/jarvis/server/__main__.py ]; then \
		cd backend && uv run python -m jarvis.server; \
	else \
		echo "dev-api: not implemented yet (implemented in T-1)"; \
	fi

dev-worker:
	@if [ -f backend/src/jarvis/pipeline/__main__.py ]; then \
		cd backend && uv run python -m jarvis.pipeline; \
	else \
		echo "dev-worker: not implemented yet (implemented in T-1)"; \
	fi

dev-web:
	@if [ -f frontend/src/main.tsx ]; then \
		cd frontend && npm run dev; \
	else \
		echo "dev-web: not implemented yet (implemented in T-8)"; \
	fi
