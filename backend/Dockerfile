# Build from the REPO ROOT (this Dockerfile needs both backend/ and
# frontend/ as build inputs):
#
#   docker build -f backend/Dockerfile -t jarvisforge:local .
#
# Two Fly.io process groups run this same image (see fly.toml):
#   web    -> the default CMD below (uvicorn serving scripts/serve_web.py)
#   worker -> fly.toml overrides CMD with the LiveKit agents worker
#             (python -m jarvis.pipeline.worker start)

# ---- stage 1: build the Vite SPA ------------------------------------------
FROM node:22-slim AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: build the Python backend into a venv ------------------------
# A separate build stage (with a compiler toolchain available) so the final
# runtime stage can stay toolchain-free -- we only copy the finished venv
# out of here, never gcc/build-essential itself.
FROM python:3.12-slim AS backend-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src

# Install the package (not just its dependencies) into a plain venv so
# `import jarvis` works anywhere the venv's Python runs -- no PYTHONPATH
# or sys.path hacks needed for `python -m jarvis.pipeline.worker`.
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache .

# ---- stage 3: runtime -------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=backend-build /opt/venv /opt/venv
COPY scripts/ ./scripts/
COPY --from=web-build /web/dist ./web/

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    JARVIS_WEB_DIST=/app/web

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Default process = web. fly.toml's `worker` process group overrides this
# with: python -m jarvis.pipeline.worker start
CMD ["uvicorn", "scripts.serve_web:app", "--host", "0.0.0.0", "--port", "8080"]
