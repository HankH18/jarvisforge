"""GET /healthz: trivial liveness probe (plan-gap fix for T-9's smoke script).

T-9's file scope (fly.toml, backend/Dockerfile, scripts/**, .github/workflows/**,
docs/latency-report.md) cannot add a route to backend/src/jarvis/server/, so the
endpoint has to land in T-1 or T-9's "health endpoint 200" acceptance item is
unbuildable. Kept dumb on purpose: no dependency/DB checks.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from jarvis.server.app import app


def test_healthz_returns_200_with_a_status_body():
    client = TestClient(app)

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
