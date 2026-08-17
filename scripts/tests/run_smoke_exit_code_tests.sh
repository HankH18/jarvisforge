#!/usr/bin/env bash
# run_smoke_exit_code_tests.sh (T-23) - tests scripts/smoke_deploy.sh's OWN
# exit-code logic, not a real deployment.
#
# scripts/smoke_deploy.sh's worker check shells out to `flyctl` (and its
# healthz/token checks shell out to `curl`). This machine has flyctl
# installed but UNAUTHENTICATED (`flyctl auth whoami` -> "Error: no access
# token available.") and there is no reachable jarvisforge deployment to
# smoke-test against, so a "real" end-to-end run cannot exercise the
# healthy-deployment or crash-looping-worker paths at all. Instead, this
# harness puts a stub `flyctl` (and a stub `curl`, so healthz/token are
# deterministic and offline too) earlier on PATH than any real binary, and
# drives smoke_deploy.sh through four scripted flyctl responses. This is a
# test of smoke_deploy.sh's own control flow given a known input, exactly
# what T-23 acceptance #3 asks for -- it is NOT a substitute for smoke-
# testing a live deployment.
#
# The stub `flyctl` command SHAPES used below (`flyctl machine list -a
# <app> -j`, the "Error: no access token available... Please login with
# 'flyctl auth login'" text) were confirmed against the real, installed
# flyctl v0.4.45 binary in this environment (its --help output and its
# actual unauthenticated error text). The stub's JSON PAYLOAD SHAPE
# (id/state/config.metadata.fly_process_group/checks/events, with "events"
# entries carrying type/timestamp) follows Fly's published Machines API
# machine-resource schema, which smoke_deploy.sh's own comments already
# document as unverified against a live authenticated call in this
# environment -- this harness does not change that; it tests smoke_deploy.sh
# against that documented, assumed shape.
#
# T-23's four cases, each asserting an exact exit code:
#   1. no_credentials      -> non-zero (flyctl unauthenticated)
#   2. healthy_deployment  -> zero     (worker started, no recent exit)
#   3. crash_looping       -> non-zero (worker started, but exited 5s ago)
#   4. worker_absent       -> non-zero (no worker-group machine at all)
#
# T-32 adds six more (5-10), each asserting an exit code AND a distinct,
# greppable named-reason substring in smoke_deploy.sh's own output -- an
# audit found the worker check's embedded parser fail-OPEN on a missing or
# unparseable exit-event timestamp (silently skipped, so a real crash
# signal could vanish behind a green verdict) and crash outright on wrong
# JSON shapes ("events" as a non-list, or a non-dict entry inside it),
# which the shell catch-all then reported as an unhelpful, unnamed "no
# parseable verdict" instead of naming what went wrong:
#   5. exit_event_garbage_timestamp -> non-zero + named reason (the
#      inversion case -- pre-T-32 this exited 0, silently healthy)
#   6. exit_event_missing_timestamp -> non-zero + named reason
#   7. events_as_dict                -> non-zero + named reason (not the
#      generic crash-derived catch-all)
#   8. events_non_dict_entry         -> non-zero + named reason (not the
#      generic crash-derived catch-all)
#   9. machine_missing_state         -> non-zero (T-23/pre-existing
#      behavior, pinned down unchanged by this addition)
#  10. live_shape_snapshot           -> zero (a real, sanitized, committed
#      snapshot from a live authenticated flyctl -- proves the hardening
#      above does not false-alarm on a real, legitimate payload)
#
# T-35 adds two more (11-12), covering smoke_deploy.sh's check b after Clerk
# auth was enabled on the deployment: an unauthenticated POST /api/token
# correctly 401ing is now itself a PASS condition (see smoke_deploy.sh's own
# check-b comment), not a regression to chase back to 200. Both cases here
# use a healthy worker (case 2's flyctl body) so the asserted exit code and
# output substring are testing ONLY check b's new branch, the same isolation
# principle cases 1-10 already use for check c:
#   11. token_endpoint_gated_401     -> zero + PASS naming the Clerk gate
#       (the exact 401 + "missing Authorization header" body Clerk's own
#       backend/src/jarvis/auth/clerk.py::_bearer_token emits for a
#       header-less request -- see that module). Proves the script accepts
#       the new, correct, authenticated-mode behavior instead of failing it.
#   12. token_endpoint_unexpected_401 -> non-zero + named reason (a 401 with
#       an UNRELATED body, e.g. from a misrouted proxy). Proves the script
#       does not treat every 401 as "the Clerk gate", only the one whose
#       body actually names it -- without this case, a check-b bug that
#       passed on ANY 401 would go undetected.
#
# A thirteenth, OPTIONAL case (live_fixture_local) runs only if a freshly,
# locally captured live fixture is present on disk -- see
# docs/ops-smoke-test.md for the one-step capture instruction. It is never
# committed (flyctl auth is a human-local credential, not something CI or a
# fresh checkout has), so its absence is normal, not a failure.
#
# Usage: bash scripts/tests/run_smoke_exit_code_tests.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_SCRIPT="${REPO_ROOT}/scripts/smoke_deploy.sh"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

FAILURES=0
CASES_RUN=0

# ---------------------------------------------------------------------------
# Stub `curl`: shared by every case below. smoke_deploy.sh only ever calls
# curl in exactly two shapes (GET .../healthz, POST .../api/token with a
# -d '{"identity":...,"room":null}' body) -- this stub answers healthz with a
# constant 200/empty-body PASS always, and answers /api/token with a 200 +
# valid-JWT-shaped body (smoke_deploy.sh's ANONYMOUS-mode path, T-9) by
# default, UNLESS the two env vars SMOKE_STUB_TOKEN_STATUS/
# SMOKE_STUB_TOKEN_BODY are set in the environment the stub curl runs in --
# then it answers /api/token with exactly that status/body instead (used by
# the T-35 gated-401 cases below to simulate Clerk's AUTHENTICATED mode).
# For every case that leaves those two env vars unset, check b stays a
# constant PASS exactly as before, so cases 1-10's asserted exit codes are
# still testing ONLY the worker check's (check c's) exit-code contribution
# -- exactly what T-23 acceptance #3 is about, unchanged by T-35.
# ---------------------------------------------------------------------------
write_stub_curl() {
    local bin_dir="$1"
    cat >"${bin_dir}/curl" <<'CURL_STUB'
#!/usr/bin/env bash
# Stub curl for run_smoke_exit_code_tests.sh - answers smoke_deploy.sh's two
# known call shapes; not a general curl replacement.
out_file=""
data=""
url=""
prev=""
for arg in "$@"; do
    case "$prev" in
        -o) out_file="$arg" ;;
        -d) data="$arg" ;;
    esac
    prev="$arg"
    url="$arg"
done

case "$url" in
    */healthz)
        printf '' >"$out_file"
        printf '200'
        ;;
    */api/token)
        if [ -n "${SMOKE_STUB_TOKEN_STATUS:-}" ]; then
            # T-35: simulate Clerk's AUTHENTICATED mode (or any other
            # scripted /api/token response) instead of the default
            # ANONYMOUS-mode 200+JWT below.
            printf '%s' "${SMOKE_STUB_TOKEN_BODY:-}" >"$out_file"
            printf '%s' "${SMOKE_STUB_TOKEN_STATUS}"
        else
            identity="$(printf '%s' "$data" | sed -E 's/.*"identity":"([^"]*)".*/\1/')"
            header='{"alg":"none","typ":"JWT"}'
            payload="$(printf '{"sub":"%s","video":{"roomJoin":true,"room":"smoke-test"},"exp":9999999999}' "$identity")"
            b64() { python3 -c "import base64,sys; print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).decode().rstrip('='))"; }
            h="$(printf '%s' "$header" | b64)"
            p="$(printf '%s' "$payload" | b64)"
            token="${h}.${p}.stubsig"
            printf '{"token":"%s","url":"wss://smoke-test.invalid"}' "$token" >"$out_file"
            printf '200'
        fi
        ;;
    *)
        printf '' >"$out_file"
        printf '000'
        ;;
esac
CURL_STUB
    chmod +x "${bin_dir}/curl"
}

# ---------------------------------------------------------------------------
# run_case <name> <expect: zero|nonzero|any> <flyctl stub body...> \
#          [must_contain] [must_not_contain] [token_status] [token_body]
# Writes the given flyctl stub body to a fresh bin dir (with the shared curl
# stub), runs smoke_deploy.sh with that bin dir first on PATH, and asserts
# the exit code matches expectation (unless expect="any", added by T-32 for
# cases whose exit code is not fixed -- see live_fixture_local below).
#
# T-32 additions (must_contain / must_not_contain, both optional, both
# purely additive -- omitting them reproduces the exact original
# zero/nonzero-only behavior the four T-23 cases below already rely on):
# assert a substring IS or IS NOT present in smoke_deploy.sh's own output.
# T-32 needs this because "the probe fails" is not the whole acceptance --
# it must fail with a distinct, greppable, NAMED reason, not just a
# nonzero exit code that could just as well come from a crash.
#
# T-35 additions (token_status / token_body, both optional, both purely
# additive -- omitting them reproduces the exact original behavior every
# pre-existing case below relies on): when token_status is non-empty, it and
# token_body are exported as SMOKE_STUB_TOKEN_STATUS/SMOKE_STUB_TOKEN_BODY
# for the stub curl (see write_stub_curl above) to answer /api/token with
# INSTEAD of its default 200+JWT ANONYMOUS-mode response -- used by the
# gated-401 cases below to simulate Clerk's AUTHENTICATED mode without
# touching the ten existing cases' check-b-is-a-constant-PASS assumption.
# ---------------------------------------------------------------------------
run_case() {
    local name="$1"
    local expect="$2"
    local flyctl_body="$3"
    local must_contain="${4:-}"
    local must_not_contain="${5:-}"
    local token_status="${6:-}"
    local token_body="${7:-}"

    local case_dir="${WORK_DIR}/${name}"
    local bin_dir="${case_dir}/bin"
    mkdir -p "$bin_dir"
    write_stub_curl "$bin_dir"

    {
        printf '#!/usr/bin/env bash\n'
        printf '%s\n' "$flyctl_body"
    } >"${bin_dir}/flyctl"
    chmod +x "${bin_dir}/flyctl"

    local out_file="${case_dir}/output.log"
    local rc=0
    if [ -n "$token_status" ]; then
        PATH="${bin_dir}:${PATH}" \
            SMOKE_STUB_TOKEN_STATUS="$token_status" \
            SMOKE_STUB_TOKEN_BODY="$token_body" \
            bash "$SMOKE_SCRIPT" "http://smoke-test.invalid" >"$out_file" 2>&1 || rc=$?
    else
        PATH="${bin_dir}:${PATH}" bash "$SMOKE_SCRIPT" "http://smoke-test.invalid" >"$out_file" 2>&1 || rc=$?
    fi

    CASES_RUN=$((CASES_RUN + 1))
    local ok=1
    local why=""
    case "$expect" in
        zero)
            [ "$rc" -eq 0 ] || { ok=0; why="expected exit 0, got ${rc}"; }
            ;;
        nonzero)
            [ "$rc" -ne 0 ] || { ok=0; why="expected nonzero exit, got 0"; }
            ;;
        any)
            : # T-32: exit code intentionally not asserted for this case
            ;;
        *)
            printf 'BUG in test harness: unknown expectation %s\n' "$expect" >&2
            exit 2
            ;;
    esac

    if [ "$ok" -eq 1 ] && [ -n "$must_contain" ] && ! grep -qF -- "$must_contain" "$out_file"; then
        ok=0
        why="expected output to contain: \"${must_contain}\""
    fi
    if [ "$ok" -eq 1 ] && [ -n "$must_not_contain" ] && grep -qF -- "$must_not_contain" "$out_file"; then
        ok=0
        why="expected output NOT to contain: \"${must_not_contain}\""
    fi

    if [ "$ok" -eq 1 ]; then
        printf 'PASS: %-26s exit=%-3s expected=%s\n' "$name" "$rc" "$expect"
    else
        FAILURES=$((FAILURES + 1))
        printf 'FAIL: %-26s exit=%-3s expected=%s (%s)\n' "$name" "$rc" "$expect" "$why"
        printf '  --- smoke_deploy.sh output ---\n'
        sed 's/^/  /' "$out_file"
        printf '  -------------------------------\n'
    fi
}

# ---------------------------------------------------------------------------
# Case 1: no credentials. Mirrors this machine's real, observed flyctl
# behavior: `flyctl auth whoami` -> "Error: no access token available.
# Please login with 'flyctl auth login'" (exit non-zero).
# ---------------------------------------------------------------------------
run_case "no_credentials" "nonzero" '
echo "Error: no access token available. Please login with '"'"'flyctl auth login'"'"'" >&2
exit 1
'

# ---------------------------------------------------------------------------
# Case 2: healthy deployment. One worker machine, state=started, no exit
# events at all -> smoke_deploy.sh must confirm it as SERVING and exit 0.
# ---------------------------------------------------------------------------
run_case "healthy_deployment" "zero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[]},
  {"id":"web1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[],"events":[]}
]
JSON
'

# ---------------------------------------------------------------------------
# Case 3: crash-looping worker. state=started (exactly the T-9 postmortem
# shape: a crash-looping machine still reads "started" when sampled), but
# its event history shows an "exit" 5 seconds ago -- well inside the
# default 300s stability window -- so smoke_deploy.sh must NOT treat
# state=started alone as proof of serving, and must exit non-zero.
# ---------------------------------------------------------------------------
run_case "crash_looping_worker" "nonzero" '
now_ms=$(python3 -c "import time; print(int(time.time()*1000))")
exit_ms=$((now_ms - 5000))
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[{"type":"exit","status":"failed","timestamp":${exit_ms}}]},
  {"id":"web1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[],"events":[]}
]
JSON
'

# ---------------------------------------------------------------------------
# Case 4: worker absent. Only a web machine exists; no "worker" process
# group machine at all -> smoke_deploy.sh must exit non-zero.
# ---------------------------------------------------------------------------
run_case "worker_absent" "nonzero" '
cat <<JSON
[
  {"id":"web1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[],"events":[]}
]
JSON
'

# =============================================================================
# T-32: wrong-JSON-shape probes. An audit found the worker check's embedded
# parser fail-OPEN on a missing/unparseable exit-event timestamp (silently
# skipped -> exactly the crash signal this check exists to catch could
# vanish behind a green verdict), and crash outright on "events" as a
# non-list or a non-dict entry inside events[] -- which the shell catch-all
# then reported as an unhelpful, unnamed "no parseable verdict" rather than
# a named failure. T-23's completion commit claimed probes like these
# already "fail loudly", but none were ever committed here. These are.
#
# Cases 5-8 assert BOTH the exit code AND a distinct, greppable named
# reason string in the output -- a nonzero exit alone would not distinguish
# "correctly detected and named" from "crashed and got caught by the
# generic catch-all", which is exactly the failure mode being closed here.
# =============================================================================

# ---------------------------------------------------------------------------
# Case 5: exit event with a GARBAGE (non-numeric) timestamp. This is the
# inversion case: proven by hand (see the T-32 completion report) that the
# pre-T-32 smoke_deploy.sh exits 0 (silently healthy) for this exact input
# -- the fail-open bug -- while the fixed script exits nonzero with a named
# reason. One worker machine, state=started, its only event is a same-
# machine "exit" whose timestamp is not parseable as a number at all.
# ---------------------------------------------------------------------------
run_case "exit_event_garbage_timestamp" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[{"type":"exit","status":"failed","timestamp":"not-a-timestamp"}]}
]
JSON
' "UNKNOWN: exit event timestamp unparseable"

# ---------------------------------------------------------------------------
# Case 6: exit event with the timestamp key missing entirely (not just
# malformed) -- the "missing" half of "missing or unparseable timestamp".
# ---------------------------------------------------------------------------
run_case "exit_event_missing_timestamp" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[{"type":"exit","status":"failed"}]}
]
JSON
' "UNKNOWN: exit event timestamp unparseable"

# ---------------------------------------------------------------------------
# Case 7: "events" is a JSON object (dict), not an array -- the shape this
# ticket's acceptance criteria calls out by name. Pre-T-32 this iterated
# the dict's string keys and crashed on `e.get(...)` inside the embedded
# parser; the shell catch-all converted that crash into the generic,
# unnamed "no parseable verdict" failure instead of a named one.
# ---------------------------------------------------------------------------
run_case "events_as_dict" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":{"type":"exit","timestamp":123}}
]
JSON
' "UNKNOWN: events field is not a list" "no parseable verdict"

# ---------------------------------------------------------------------------
# Case 8: "events" is a list, but one entry in it is not an object (e.g. a
# bare string) -- same crash-then-generic-catch-all failure mode as case 7,
# one level deeper.
# ---------------------------------------------------------------------------
run_case "events_non_dict_entry" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":["not-a-dict"]}
]
JSON
' "UNKNOWN: events[] contains a non-dict entry" "no parseable verdict"

# ---------------------------------------------------------------------------
# Case 9: a machine entry with NO "state" key at all. This must continue to
# fail exactly as it does today (T-32 acceptance: "a machine entry missing
# state continues to fail as it does today") -- no code change was made for
# this shape, this case only pins down that it was not accidentally broken
# by the events-shape hardening above.
# ---------------------------------------------------------------------------
run_case "machine_missing_state" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[]}
]
JSON
' "NONE in state 'started'"

# ---------------------------------------------------------------------------
# Case 10: a real, sanitized snapshot captured from a live authenticated
# `flyctl machine list -a jarvisforge -j` (flyctl v0.4.83, 2026-08-14) --
# see docs/ops-smoke-test.md for how to re-capture this. Real machine IDs,
# private IPs, instance IDs and image digests are stripped; the field
# NAMES, TYPES and event ordering are exactly what flyctl returned. One
# worker machine was mid-deploy-cycle "stopped", the other "started" with
# no exit event -- smoke_deploy.sh must call this SERVING, proving the
# T-32 hardening does not false-alarm on real, legitimate payloads.
# ---------------------------------------------------------------------------
run_case "live_shape_snapshot" "zero" '
cat <<JSON
[
  {"id":"w-sanitized-1","name":"jarvisforge-worker","state":"stopped","config":{"metadata":{"fly_process_group":"worker"}},"checks":null,"events":[{"type":"update","status":"stopped","source":"flyd","timestamp":1786732219279},{"type":"launch","status":"created","source":"user","timestamp":1786732209227},{"type":"launch","status":"pending","source":"flyd","timestamp":1786732209146}]},
  {"id":"w-sanitized-2","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":null,"events":[{"type":"start","status":"started","source":"flyd","timestamp":1786732221086},{"type":"launch","status":"created","source":"user","timestamp":1786732209225},{"type":"launch","status":"pending","source":"flyd","timestamp":1786732209101}]},
  {"id":"web-sanitized-1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[{"name":"servicecheck-00-http-8080","status":"passing","output":"{\"status\":\"ok\"}","updated_at":"2026-08-14T18:30:50.875Z"}],"events":[{"type":"start","status":"started","source":"flyd","timestamp":1786732246625},{"type":"launch","status":"created","source":"user","timestamp":1786732234966},{"type":"launch","status":"pending","source":"flyd","timestamp":1786732234840}]},
  {"id":"web-sanitized-2","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[{"name":"servicecheck-00-http-8080","status":"passing","output":"{\"status\":\"ok\"}","updated_at":"2026-08-14T18:30:26.75Z"}],"events":[{"type":"start","status":"started","source":"flyd","timestamp":1786732222418},{"type":"launch","status":"created","source":"user","timestamp":1786732209182},{"type":"launch","status":"pending","source":"flyd","timestamp":1786732209052}]}
]
JSON
' "confirmed SERVING"

# =============================================================================
# T-35: check-b gated-401 cases. Both use case 2's healthy-worker flyctl body
# (unchanged from healthy_deployment above) so the worker check stays a
# constant PASS and the asserted exit code/output is testing ONLY check b's
# new 401-handling branch -- the mirror-image isolation of what cases 1-10
# already do for check c.
# =============================================================================

# ---------------------------------------------------------------------------
# Case 11: /api/token answers 401 with the exact body Clerk's own
# backend/src/jarvis/auth/clerk.py::_bearer_token emits for a request with no
# Authorization header (the observed live-deployment failure this update was
# written to fix: `{"detail":"missing Authorization header"}`). This is now
# CORRECT, desired behavior (Clerk auth enabled) and must PASS, not fail --
# smoke_deploy.sh must exit zero and its own output must say the gate is
# confirmed enabled.
# ---------------------------------------------------------------------------
run_case "token_endpoint_gated_401" "zero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[]},
  {"id":"web1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[],"events":[]}
]
JSON
' "Clerk auth is enabled and the endpoint correctly rejects" "" \
  "401" '{"detail":"missing Authorization header"}'

# ---------------------------------------------------------------------------
# Case 12: /api/token answers 401 too, but with a body that does NOT name the
# Clerk gate (simulating some unrelated cause of a 401 -- a misrouted proxy,
# unrelated middleware). smoke_deploy.sh must NOT treat this as "Clerk auth
# confirmed enabled" just because the status code matches; it must fail, and
# must not print the gated-401 PASS message case 11 asserts for the real
# reason. Without this case, a check-b bug that treated ANY 401 as the Clerk
# gate (dropping the reason check entirely) would pass case 11 and go
# undetected.
# ---------------------------------------------------------------------------
run_case "token_endpoint_unexpected_401" "nonzero" '
cat <<JSON
[
  {"id":"w1","name":"jarvisforge-worker","state":"started","config":{"metadata":{"fly_process_group":"worker"}},"checks":[],"events":[]},
  {"id":"web1","name":"jarvisforge-web","state":"started","config":{"metadata":{"fly_process_group":"web"}},"checks":[],"events":[]}
]
JSON
' "not for the expected Clerk-gate reason" "Clerk auth is enabled and the endpoint correctly rejects" \
  "401" '{"detail":"invalid token"}'

# ---------------------------------------------------------------------------
# Case 13 (OPTIONAL, not committed): a fresh local capture from whoever
# next runs `flyctl auth login` and follows the one-step instruction in
# docs/ops-smoke-test.md. Only runs if that file exists; it is gitignored
# (matches the repo's existing `*.local` pattern) so it never becomes a
# tracked, possibly-stale artifact between real captures. Its true health
# verdict is whatever production actually was at capture time, which this
# harness cannot know in advance, so the exit code is NOT asserted
# ("any") -- what IS asserted is that a real payload never falls through to
# the generic crash-derived "no parseable verdict" catch-all, which would
# mean an unanticipated real-world shape reached the parser undetected:
# exactly the regression class T-32 closes.
# ---------------------------------------------------------------------------
LIVE_FIXTURE="${SMOKE_LIVE_FIXTURE:-${REPO_ROOT}/scripts/tests/fixtures/live_flyctl_machine_list.json.local}"
if [ -f "$LIVE_FIXTURE" ]; then
    run_case "live_fixture_local" "any" "cat '${LIVE_FIXTURE}'" "" "no parseable verdict"
else
    printf 'INFO: no local live fixture at %s -- skipping optional live-shape case (see docs/ops-smoke-test.md to capture one).\n' "$LIVE_FIXTURE"
fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf 'SUMMARY: %d/%d smoke_deploy.sh exit-code cases passed.\n' "$CASES_RUN" "$CASES_RUN"
    exit 0
else
    printf 'SUMMARY: %d/%d smoke_deploy.sh exit-code case(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
    exit 1
fi
