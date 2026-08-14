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
# Four cases, each asserting an exact exit code:
#   1. no_credentials      -> non-zero (flyctl unauthenticated)
#   2. healthy_deployment  -> zero     (worker started, no recent exit)
#   3. crash_looping       -> non-zero (worker started, but exited 5s ago)
#   4. worker_absent       -> non-zero (no worker-group machine at all)
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
# -d '{"identity":...,"room":null}' body) -- this stub answers both with a
# 200 and a body that PASSES smoke_deploy.sh's own healthz/token checks
# (including its JWT-shaped-payload decode, which requires the "sub" claim
# to echo back the identity smoke_deploy.sh sent). That keeps checks a/b
# constant-PASS in every case, so the four asserted exit codes below are
# testing ONLY the worker check's (check c's) exit-code contribution --
# exactly what T-23 acceptance #3 is about.
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
        identity="$(printf '%s' "$data" | sed -E 's/.*"identity":"([^"]*)".*/\1/')"
        header='{"alg":"none","typ":"JWT"}'
        payload="$(printf '{"sub":"%s","video":{"roomJoin":true,"room":"smoke-test"},"exp":9999999999}' "$identity")"
        b64() { python3 -c "import base64,sys; print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).decode().rstrip('='))"; }
        h="$(printf '%s' "$header" | b64)"
        p="$(printf '%s' "$payload" | b64)"
        token="${h}.${p}.stubsig"
        printf '{"token":"%s","url":"wss://smoke-test.invalid"}' "$token" >"$out_file"
        printf '200'
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
# run_case <name> <expect: zero|nonzero> <flyctl stub body...>
# Writes the given flyctl stub body to a fresh bin dir (with the shared curl
# stub), runs smoke_deploy.sh with that bin dir first on PATH, and asserts
# the exit code matches expectation.
# ---------------------------------------------------------------------------
run_case() {
    local name="$1"
    local expect="$2"
    local flyctl_body="$3"

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
    PATH="${bin_dir}:${PATH}" bash "$SMOKE_SCRIPT" "http://smoke-test.invalid" >"$out_file" 2>&1 || rc=$?

    CASES_RUN=$((CASES_RUN + 1))
    local ok=1
    case "$expect" in
        zero)
            [ "$rc" -eq 0 ] || ok=0
            ;;
        nonzero)
            [ "$rc" -ne 0 ] || ok=0
            ;;
        *)
            printf 'BUG in test harness: unknown expectation %s\n' "$expect" >&2
            exit 2
            ;;
    esac

    if [ "$ok" -eq 1 ]; then
        printf 'PASS: %-20s exit=%-3s expected=%s\n' "$name" "$rc" "$expect"
    else
        FAILURES=$((FAILURES + 1))
        printf 'FAIL: %-20s exit=%-3s expected=%s\n' "$name" "$rc" "$expect"
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

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf 'SUMMARY: %d/%d smoke_deploy.sh exit-code cases passed.\n' "$CASES_RUN" "$CASES_RUN"
    exit 0
else
    printf 'SUMMARY: %d/%d smoke_deploy.sh exit-code case(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
    exit 1
fi
