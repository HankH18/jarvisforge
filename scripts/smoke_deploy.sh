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
#   c. worker liveness via `flyctl machine list` - proves the worker process
#      group is actually SERVING, not merely present: it must have a machine
#      in state "started" with no recent crash/restart in its Fly event
#      history (see the check's own comment below for why state alone is not
#      enough, and docs/ops-smoke-test.md for the credentials this needs).
#
# There is no such thing as a SKIP in this script (T-23): every condition that
# used to print SKIP-and-move-on (flyctl missing, flyctl unauthenticated,
# unparseable output, python3 missing) now calls fail(). An unrunnable check
# against a target that is supposed to be deployed is a failure, not a pass -
# a green exit code here means every check actually ran and actually passed.
#
# Exit code is non-zero iff at least one check FAILed (which now includes
# every "could not be checked" condition above).

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
# c. worker liveness via `flyctl machine list --json`
#
# Grepping for the word "worker" is NOT a check: a process group that exists
# in fly.toml but was never scaled up still prints "worker" with a machine
# count of zero. And machine STATE ALONE is not a check either: T-9's own
# postmortem (see fly.toml's [[vm]] comment) found the worker OOM-crashing
# and restarting in a loop while `flyctl status` kept reading state
# "started" -- it happened to be sampled mid-loop, right after a restart.
# The worker also takes no public traffic (fly.toml [http_service]
# processes=["web"] only), so it has no Fly-configured health check either;
# Fly's `checks` array will normally be empty for it.
#
# The signal actually used to prove SERVING (not merely present): each
# worker machine's recent Fly Machines-API event history, which `flyctl
# machine list -j` surfaces per Fly's published Machines API resource shape
# (id/state/config/events/checks) -- state alone hides exactly the
# restart-loop T-9 hit; a same-second "exit" event does not. A machine only
# counts as serving if it is state=="started" AND has recorded no "exit"
# event within the last $SMOKE_WORKER_STABLE_WINDOW_SECONDS seconds, AND any
# Fly-configured checks it does have all report "passing". NOTE: this event-
# field assumption is NOT verified against a live authenticated flyctl in
# this environment (flyctl is installed but unauthenticated here) -- see
# docs/ops-smoke-test.md for that caveat and how to re-verify it once
# credentials exist. What IS verified against the real installed flyctl
# binary is the *command shape* itself (`flyctl machine list -a <app> -j`
# argument-parses and reaches the network call; `flyctl machine status
# --json` does NOT exist in this flyctl version, which is why machine list
# is used instead of a per-machine status call).
#
# Per T-23 acceptance #1: every branch that cannot positively establish
# SERVING calls fail(), never a non-failing skip. A fresh deploy will
# legitimately fail this check for the first $SMOKE_WORKER_STABLE_WINDOW_SECONDS
# seconds after it starts -- that is intentional, not a bug: "just started"
# and "stably serving" are different claims.
# ---------------------------------------------------------------------------
SMOKE_WORKER_STABLE_WINDOW_SECONDS="${SMOKE_WORKER_STABLE_WINDOW_SECONDS:-300}"

if ! command -v flyctl >/dev/null 2>&1; then
    fail "worker liveness check - flyctl is not installed; cannot verify the worker process group is serving (install flyctl, or run \`flyctl machine list -a ${FLY_APP_NAME} -j\` by hand)"
elif ! command -v python3 >/dev/null 2>&1; then
    fail "worker liveness check - python3 is not available to parse \`flyctl machine list --json\`; cannot verify the worker process group is serving"
else
    fly_machines_output="$(mktemp)"
    if flyctl machine list -a "$FLY_APP_NAME" -j >"$fly_machines_output" 2>&1; then
        worker_verdict="$(python3 - "$fly_machines_output" "$SMOKE_WORKER_STABLE_WINDOW_SECONDS" <<'PY' 2>/dev/null || true
import json, sys, time

try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except Exception:
    print("UNKNOWN||||could not parse `flyctl machine list --json` output as JSON")
    raise SystemExit(0)

stable_window = float(sys.argv[2])
machines = data if isinstance(data, list) else (data.get("Machines") or data.get("machines") or [])


def group_of(m):
    meta = (m.get("config") or {}).get("metadata") or {}
    return (
        m.get("process_group")
        or m.get("ProcessGroup")
        or meta.get("fly_process_group")
        or ""
    )


workers = [m for m in machines if group_of(m).lower() == "worker"]
total = len(workers)
started = [m for m in workers if str(m.get("state") or m.get("State") or "").lower() == "started"]

now = time.time()
serving = 0
bad_reasons = []

for m in started:
    mid = m.get("id") or m.get("ID") or "?"
    checks = m.get("checks") or []
    failing_checks = [c for c in checks if str(c.get("status") or "").lower() not in ("passing", "")]
    if failing_checks:
        c0 = failing_checks[0]
        bad_reasons.append(f"{mid} has a non-passing Fly health check ({c0.get('name')}={c0.get('status')})")
        continue

    events = m.get("events")
    if events is None:
        bad_reasons.append(f"{mid} - no event history in flyctl output; cannot confirm it has not just crash-restarted")
        continue

    recent_exit_age = None
    for e in events:
        if str(e.get("type") or "").lower() != "exit":
            continue
        try:
            ts = float(e.get("timestamp"))
        except (TypeError, ValueError):
            continue
        ts_sec = ts / 1000.0 if ts > 1e12 else ts
        age = now - ts_sec
        if age < 0:
            continue
        if recent_exit_age is None or age < recent_exit_age:
            recent_exit_age = age

    if recent_exit_age is not None and recent_exit_age <= stable_window:
        bad_reasons.append(f"{mid} exited {int(recent_exit_age)}s ago and is now reporting state=started again -- crash-looping, not serving")
        continue

    serving += 1

if serving > 0:
    print(f"OK|{total}|{len(started)}|{serving}|")
elif started:
    reason = "; ".join(bad_reasons or ["no started machine could be confirmed stable"])
    print(f"CRASHLOOP|{total}|{len(started)}|{serving}|{reason}")
else:
    print(f"NOSTART|{total}|{len(started)}|{serving}|")
PY
)"
        IFS='|' read -r w_tag w_total w_started w_serving w_reason <<<"$worker_verdict"
        case "$w_tag" in
            OK)
                pass "worker process group has ${w_serving}/${w_started} machine(s) confirmed SERVING (state=started, no exit event in the last ${SMOKE_WORKER_STABLE_WINDOW_SECONDS}s, any Fly health checks passing)"
                ;;
            CRASHLOOP)
                fail "worker process group is NOT serving -- ${w_reason}"
                ;;
            NOSTART)
                if [ "${w_total:-0}" -eq 0 ]; then
                    fail "no 'worker' process-group machine exists for app ${FLY_APP_NAME} (the worker is not deployed)"
                else
                    fail "the 'worker' process group for ${FLY_APP_NAME} has ${w_total} machine(s) but NONE in state 'started' - scale it up (\`flyctl scale count worker=1 -a ${FLY_APP_NAME}\`)"
                fi
                ;;
            UNKNOWN)
                fail "worker liveness check could not be completed (${w_reason:-unparseable flyctl output}); an unrunnable check is not a pass"
                ;;
            *)
                fail "worker liveness check produced no parseable verdict (raw: ${worker_verdict:-<empty>})"
                ;;
        esac
    else
        if grep -qi 'no access token available\|not authenticated\|auth login' "$fly_machines_output"; then
            fail "worker liveness check - flyctl is not authenticated in this environment (\`flyctl auth login\` required); cannot verify the worker is serving, so this check FAILS rather than silently passing"
        else
            fail "worker liveness check - \`flyctl machine list -a ${FLY_APP_NAME} -j\` failed ($(head -c 200 "$fly_machines_output" | tr '\n' ' '))"
        fi
    fi
    rm -f "$fly_machines_output"
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
