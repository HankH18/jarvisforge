#!/usr/bin/env bash
# smoke_deploy.sh - smoke-test a deployed (or locally containerized) jarvisforge
# web process.
#
# Usage:
#   bash scripts/smoke_deploy.sh [URL]
#
# Target URL resolution order: $1 > $SMOKE_URL > https://jarvisforge.fly.dev
#
# Checks:
#   a. GET  /healthz     -> HTTP 200
#   b. POST /api/token   -> HTTP 200, JSON body with non-empty string "token"
#                           (3 dot-separated JWT segments) and "url" (starts "ws")
#   c. worker registration via `flyctl status` - DEGRADES GRACEFULLY: if flyctl
#      is missing or unauthenticated this prints a SKIP (not a PASS) explaining
#      what a human must run.
#
# Exit code is non-zero iff at least one check FAILed. SKIPs never affect the
# exit code by themselves.

set -euo pipefail

TARGET_URL="${1:-${SMOKE_URL:-https://jarvisforge.fly.dev}}"
# Strip a trailing slash so path concatenation below is unambiguous.
TARGET_URL="${TARGET_URL%/}"

FLY_APP_NAME="${FLY_APP_NAME:-jarvisforge}"

FAILURES=0
CHECKS_RUN=0

pass() {
    CHECKS_RUN=$((CHECKS_RUN + 1))
    printf 'PASS: %s\n' "$1"
}

fail() {
    CHECKS_RUN=$((CHECKS_RUN + 1))
    FAILURES=$((FAILURES + 1))
    printf 'FAIL: %s\n' "$1"
}

skip() {
    printf 'SKIP: %s\n' "$1"
}

printf 'Target: %s\n' "$TARGET_URL"
printf '\n'

# ---------------------------------------------------------------------------
# a. GET /healthz
# ---------------------------------------------------------------------------
healthz_body_file="$(mktemp)"
trap 'rm -f "$healthz_body_file" "${token_body_file:-}"' EXIT

healthz_status="000"
if ! healthz_status="$(curl -sS --max-time 10 -o "$healthz_body_file" -w '%{http_code}' \
        "${TARGET_URL}/healthz" 2>"${healthz_body_file}.err")"; then
    healthz_status="000"
fi

if [ "$healthz_status" = "200" ]; then
    pass "GET /healthz returned 200"
else
    curl_err=""
    if [ -s "${healthz_body_file}.err" ]; then
        curl_err=" ($(tr -d '\n' < "${healthz_body_file}.err"))"
    fi
    if [ "$healthz_status" = "000" ]; then
        fail "GET /healthz - request failed (connection refused or timed out)${curl_err}"
    else
        fail "GET /healthz returned ${healthz_status}, expected 200 (body: $(head -c 200 "$healthz_body_file"))"
    fi
fi
rm -f "${healthz_body_file}.err"

# ---------------------------------------------------------------------------
# b. POST /api/token
# ---------------------------------------------------------------------------
token_body_file="$(mktemp)"
SMOKE_IDENTITY="smoke-$$"

token_status="000"
if ! token_status="$(curl -sS --max-time 10 -o "$token_body_file" -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -d "{\"identity\":\"${SMOKE_IDENTITY}\",\"room\":null}" \
        "${TARGET_URL}/api/token" 2>"${token_body_file}.err")"; then
    token_status="000"
fi

if [ "$token_status" != "200" ]; then
    curl_err=""
    if [ -s "${token_body_file}.err" ]; then
        curl_err=" ($(tr -d '\n' < "${token_body_file}.err"))"
    fi
    if [ "$token_status" = "000" ]; then
        fail "POST /api/token - request failed (connection refused or timed out)${curl_err}"
    else
        fail "POST /api/token returned ${token_status}, expected 200 (body: $(head -c 200 "$token_body_file"))"
    fi
else
    token_json="$(cat "$token_body_file")"

    # Extract "token" and "url" string values without requiring jq/python to be
    # present on the operator's machine - a small, deliberately strict regex
    # grep. This only accepts a plain string value (quoted, no embedded
    # unescaped quotes); anything else (missing key, null, number, nested
    # object) fails the extraction, which is what we want for a bad-shape body.
    token_val="$(printf '%s' "$token_json" | grep -o '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/' || true)"
    url_val="$(printf '%s' "$token_json" | grep -o '"url"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/' || true)"

    if [ -z "$token_val" ]; then
        fail "POST /api/token - response has no non-empty string \"token\" field (body: $(head -c 200 "$token_body_file"))"
    elif [ -z "$url_val" ]; then
        fail "POST /api/token - response has no non-empty string \"url\" field (body: $(head -c 200 "$token_body_file"))"
    else
        # token must have exactly 3 dot-separated segments (JWT shape).
        dot_count="$(printf '%s' "$token_val" | tr -cd '.' | wc -c | tr -d ' ')"
        segments=$((dot_count + 1))

        token_shape_ok=1
        if [ "$segments" -ne 3 ]; then
            token_shape_ok=0
        fi
        IFS='.' read -r seg1 seg2 seg3 <<<"$token_val"
        if [ -z "${seg1:-}" ] || [ -z "${seg2:-}" ] || [ -z "${seg3:-}" ]; then
            token_shape_ok=0
        fi

        url_shape_ok=1
        case "$url_val" in
            ws*) : ;;
            *) url_shape_ok=0 ;;
        esac

        # Shape alone is a weak check: "aaa.bbb.ccc" passes it. Decode the
        # payload and require an actual LiveKit room grant that matches the
        # identity we asked for and has not already expired, so a stub that
        # returns a plausible-looking string cannot pass this smoke test.
        grant_detail=""
        if [ "$token_shape_ok" -eq 1 ] && command -v python3 >/dev/null 2>&1; then
            grant_detail="$(python3 - "$token_val" "$SMOKE_IDENTITY" <<'PY' 2>/dev/null || true
import base64, json, sys, time

token, identity = sys.argv[1], sys.argv[2]
try:
    seg = token.split(".")[1]
    payload = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
except Exception:
    print("BAD: token payload is not decodable base64url JSON")
    raise SystemExit(0)

video = payload.get("video") or {}
if not video.get("roomJoin"):
    print("BAD: token payload carries no video.roomJoin grant")
elif payload.get("sub") != identity:
    print(f"BAD: token subject {payload.get('sub')!r} does not match requested identity {identity!r}")
elif payload.get("exp") and float(payload["exp"]) <= time.time():
    print("BAD: token is already expired")
else:
    print(f"OK: room={video.get('room')!r} sub={payload.get('sub')!r}")
PY
)"
            case "$grant_detail" in
                BAD:*) token_shape_ok=0 ;;
            esac
        fi

        if [ "$token_shape_ok" -eq 1 ] && [ "$url_shape_ok" -eq 1 ]; then
            pass "POST /api/token returned 200 with a valid room grant and ws* url${grant_detail:+ (${grant_detail#OK: })}"
        else
            reasons=""
            if [ "$token_shape_ok" -eq 0 ]; then
                # Distinguish "wrong shape" from "right shape, no real grant" --
                # they point at completely different bugs.
                case "$grant_detail" in
                    BAD:*) reasons="${reasons}${grant_detail#BAD: }; " ;;
                    *) reasons="${reasons}token \"${token_val}\" is not 3 dot-separated non-empty segments; " ;;
                esac
            fi
            if [ "$url_shape_ok" -eq 0 ]; then
                reasons="${reasons}url \"${url_val}\" does not start with \"ws\"; "
            fi
            fail "POST /api/token - malformed token/url shape: ${reasons}"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# c. worker registration via flyctl - degrades gracefully
# ---------------------------------------------------------------------------
if ! command -v flyctl >/dev/null 2>&1; then
    skip "worker registration check - flyctl is not installed; a human must run \`flyctl status -a ${FLY_APP_NAME}\` and confirm the 'worker' process group shows a started machine"
else
    fly_status_output="$(mktemp)"
    if flyctl status -a "$FLY_APP_NAME" --json >"$fly_status_output" 2>&1; then
        # Grepping for the word "worker" is NOT a check: a process group that
        # exists in fly.toml but was never scaled up still prints "worker" with
        # a machine count of zero, and Fly does not always auto-create machines
        # for secondary process groups. Parse the machine states instead, and
        # require at least one actually STARTED worker machine.
        if command -v python3 >/dev/null 2>&1; then
            worker_summary="$(python3 - "$fly_status_output" <<'PY' 2>/dev/null || true
import json, sys

try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except Exception:
    print("UNPARSEABLE")
    raise SystemExit(0)

machines = data.get("Machines") or data.get("machines") or []


def group_of(m):
    meta = (m.get("config") or {}).get("metadata") or {}
    return (
        m.get("process_group")
        or m.get("ProcessGroup")
        or meta.get("fly_process_group")
        or ""
    )


workers = [m for m in machines if group_of(m).lower() == "worker"]
started = [m for m in workers if str(m.get("state") or m.get("State") or "").lower() == "started"]
print(f"{len(workers)} {len(started)}")
PY
)"
            if [ -z "$worker_summary" ] || [ "$worker_summary" = "UNPARSEABLE" ]; then
                skip "worker registration check - could not parse \`flyctl status --json\` output; a human must run \`flyctl status -a ${FLY_APP_NAME}\` and confirm a 'worker' machine is started"
            else
                worker_total="${worker_summary%% *}"
                worker_started="${worker_summary##* }"
                if [ "$worker_total" -eq 0 ]; then
                    fail "no 'worker' process-group machine exists for app ${FLY_APP_NAME} (the worker is not deployed)"
                elif [ "$worker_started" -eq 0 ]; then
                    fail "the 'worker' process group for ${FLY_APP_NAME} has ${worker_total} machine(s) but NONE in state 'started' - scale it up (\`flyctl scale count worker=1 -a ${FLY_APP_NAME}\`)"
                else
                    pass "worker process group has ${worker_started}/${worker_total} machine(s) in state 'started'"
                fi
            fi
        else
            skip "worker registration check - python3 not available to parse \`flyctl status --json\`; a human must confirm a 'worker' machine is started"
        fi
    else
        if grep -qi 'no access token available\|not authenticated\|auth login' "$fly_status_output"; then
            skip "worker registration check - flyctl is not authenticated in this environment; a human must run \`flyctl auth login\` then \`flyctl status -a ${FLY_APP_NAME}\` and confirm the 'worker' process group shows a started machine"
        else
            skip "worker registration check - \`flyctl status -a ${FLY_APP_NAME}\` failed ($(head -c 200 "$fly_status_output" | tr '\n' ' ')); a human must run \`flyctl status -a ${FLY_APP_NAME}\` directly and confirm the 'worker' process group shows a started machine"
        fi
    fi
    rm -f "$fly_status_output"
fi

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf 'SUMMARY: 0 failures across %d checks.\n' "$CHECKS_RUN"
    exit 0
else
    printf 'SUMMARY: %d failure(s) across %d checks.\n' "$FAILURES" "$CHECKS_RUN"
    exit 1
fi
