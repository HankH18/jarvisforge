#!/usr/bin/env bash
# load_smoke.sh - R9 load smoke (T-10): N concurrent voice-agent SESSIONS,
# each authenticated through the now Clerk-gated POST /api/token, each a
# REAL LiveKit room connection against this project's actual LiveKit Cloud
# instance ($LIVEKIT_URL) -- not a mock, not a room-count arithmetic check.
#
# Usage:
#   bash scripts/load_smoke.sh [N]     (default 10)
#
# What this proves: N distinct Clerk identities can each obtain a LiveKit
# access token from the real, auth-gated /api/token endpoint and hold a
# real WebRTC room connection open AT THE SAME TIME. "Session" here means
# the LiveKit room-join layer specifically -- a full voice turn also needs
# STT/TTS, and Cartesia (TTS) is independently down (402, credits
# exhausted) as this ticket is being built. Acceptance #4 does not require
# a working voice turn, only that concurrency at the transport layer holds,
# so this script never touches STT/TTS.
#
# Auth: the server it drives is a real local instance of
# jarvis.server.app:app (the exact production app, unmodified) started
# by this script, configured to verify Clerk session tokens in Clerk's
# documented "networkless" mode (CLERK_JWT_KEY = a PEM public key) against
# a keypair generated fresh per run. This is the SAME verification code
# path as production (jarvis.auth.clerk.require_clerk_identity ->
# clerk_backend_api.security.verify_token), just skipping the network hop
# to Clerk's JWKS -- not a stub auth layer. N distinct "user_loadsmoke_*"
# identities are minted, matching how N distinct real Clerk users would
# each carry their own session token.
#
# Honesty (T-23 precedent, see scripts/smoke_deploy.sh's own header): there
# is no SKIP in this script. Every condition that would make "N concurrent
# real sessions" unverifiable -- missing LiveKit/Clerk credentials, the
# local server failing to start, a /api/token call failing, a LiveKit room
# connection failing or timing out (including a LiveKit Cloud free-tier
# concurrency cap rejecting a join, SPEC's explicit non-goal: flag it, do
# not self-host LiveKit to work around it) -- is a FAIL with a clear reason,
# never a silent pass. A green exit code means N real sessions were
# actually held open concurrently, full stop.

set -uo pipefail

N="${1:-10}"
case "$N" in
    ''|*[!0-9]*|0) echo "FAIL: N must be a positive integer (got: '${N}')" >&2; exit 1 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

if [ ! -d "$BACKEND_DIR" ]; then
    echo "FAIL: backend/ not found at ${BACKEND_DIR} -- cannot run the load smoke." >&2
    exit 1
fi

# Load repo-root .env if the credentials this script needs aren't already
# in the environment (mirrors "cd backend && set -a && . ../.env && set +a"
# from this project's own dev workflow) -- but never silently proceed
# without them; see the check right after.
if [ -z "${LIVEKIT_URL:-}" ] || [ -z "${LIVEKIT_API_KEY:-}" ] || [ -z "${LIVEKIT_API_SECRET:-}" ]; then
    if [ -f "${REPO_ROOT}/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "${REPO_ROOT}/.env"
        set +a
    fi
fi

MISSING=""
for var in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
    if [ -z "${!var:-}" ]; then
        MISSING="${MISSING} ${var}"
    fi
done
if [ -n "$MISSING" ]; then
    echo "FAIL: missing required env var(s):${MISSING}. Cannot reach real LiveKit -- this is a failure, not a skip." >&2
    exit 1
fi

WORKDIR="$(mktemp -d)"
SERVER_LOG="${WORKDIR}/server.log"
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

# --- A fresh RSA keypair for this run's Clerk "networkless" verification.
# Real RS256 keys, not stubs -- see jarvis/auth/clerk.py and
# backend/tests/multiuser/conftest.py's RSAKeyPair for the same pattern.
if ! ( cd "$BACKEND_DIR" && uv run python3 - "$WORKDIR" <<'PY'
import sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

workdir = Path(sys.argv[1])
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
(workdir / "priv.pem").write_bytes(
    key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
)
(workdir / "pub.pem").write_bytes(
    key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
)
PY
); then
    echo "FAIL: could not generate the test signing keypair (see output above)." >&2
    exit 1
fi

# --- A free local port for the ephemeral server instance. ---
PORT="$(cd "$BACKEND_DIR" && uv run python3 -c '
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
')"
if [ -z "$PORT" ]; then
    echo "FAIL: could not allocate a local port for the ephemeral server." >&2
    exit 1
fi
BASE_URL="http://127.0.0.1:${PORT}"

# --- Start the REAL app (jarvis.server.app:app, unmodified) locally,
# configured to trust this run's keypair for Clerk verification. ---
(
    cd "$BACKEND_DIR" && \
    PYTHONPATH="${BACKEND_DIR}/src" \
    CLERK_JWT_KEY="$(cat "${WORKDIR}/pub.pem")" \
    LIVEKIT_URL="$LIVEKIT_URL" \
    LIVEKIT_API_KEY="$LIVEKIT_API_KEY" \
    LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET" \
    uv run uvicorn jarvis.server.app:app --host 127.0.0.1 --port "$PORT" \
        >"$SERVER_LOG" 2>&1
) &
SERVER_PID=$!

HEALTHY=0
for _ in $(seq 1 50); do
    if curl -sS --max-time 2 -o /dev/null -w '' "${BASE_URL}/healthz" 2>/dev/null; then
        HEALTHY=1
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.2
done

if [ "$HEALTHY" -ne 1 ]; then
    echo "FAIL: local jarvis.server.app instance never became healthy at ${BASE_URL}/healthz." >&2
    echo "--- server log ---" >&2
    cat "$SERVER_LOG" >&2 || true
    exit 1
fi

# --- N concurrent sessions: mint N Clerk-shaped tokens, call the real
# auth-gated /api/token N times concurrently, then hold N real LiveKit
# room connections open at the same time. ---
if ! ( cd "$BACKEND_DIR" && uv run python3 - "$N" "$BASE_URL" "${WORKDIR}/priv.pem" "load-smoke-$$-$(date +%s)" <<'PY'
import asyncio
import sys
import time

import httpx
import jwt as pyjwt
from livekit import rtc

N = int(sys.argv[1])
BASE_URL = sys.argv[2]
PRIVATE_KEY_PATH = sys.argv[3]
ROOM = sys.argv[4]

PRIVATE_KEY_PEM = open(PRIVATE_KEY_PATH).read()


def mint_token(sub: str) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": "https://load-smoke.test",
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + 300,
        "azp": "https://jarvis.test",
    }
    return pyjwt.encode(claims, PRIVATE_KEY_PEM, algorithm="RS256", headers={"kid": "load-smoke-kid"})


async def fetch_livekit_token(client: httpx.AsyncClient, bearer: str) -> dict:
    resp = await client.post(
        f"{BASE_URL}/api/token",
        json={"identity": "ignored-client-supplied-identity", "room": ROOM},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def open_session(livekit_url: str, livekit_token: str) -> tuple[bool, str | None]:
    room = rtc.Room()
    try:
        await asyncio.wait_for(room.connect(livekit_url, livekit_token), timeout=20)
        return True, None
    except Exception as exc:  # noqa: BLE001 - report every failure mode, don't filter
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await room.disconnect()
        except Exception:  # noqa: BLE001 - best-effort teardown only
            pass


async def main() -> int:
    identities = [f"user_loadsmoke_{i}_{int(time.time())}" for i in range(N)]
    bearer_tokens = [mint_token(ident) for ident in identities]

    async with httpx.AsyncClient() as client:
        try:
            token_results = await asyncio.gather(
                *(fetch_livekit_token(client, bt) for bt in bearer_tokens)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: minting LiveKit tokens via the auth-gated /api/token failed: {exc}")
            return 1

    print(f"minted {len(token_results)}/{N} LiveKit access tokens via the auth-gated /api/token")

    outcomes = await asyncio.gather(
        *(open_session(tr["url"], tr["token"]) for tr in token_results)
    )

    ok = 0
    for i, (success, err) in enumerate(outcomes):
        if success:
            ok += 1
            print(f"[{i}] {identities[i]}: connected")
        else:
            print(f"[{i}] {identities[i]}: FAILED - {err}")

    print(f"RESULT: {ok}/{N} concurrent real LiveKit sessions held open simultaneously")
    if ok < N:
        print(
            f"FAIL: only reached {ok}/{N} real concurrent sessions. If the failures above are "
            "LiveKit connection/participant-limit errors, this may be a LiveKit Cloud free-tier "
            "concurrency cap -- flag it; do not self-host LiveKit to work around it (explicit "
            "non-goal)."
        )
        return 1
    return 0


sys.exit(asyncio.run(main()))
PY
); then
    echo "FAIL: did not sustain ${N} concurrent LiveKit sessions (see output above)." >&2
    exit 1
fi

echo "PASS: sustained ${N} concurrent LiveKit sessions via the auth-gated /api/token."
exit 0
