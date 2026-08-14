# Smoke test and memory-integration credentials (T-23)

This is the answer to one question: **given a green run, what was actually
checked, and what wasn't?** `scripts/smoke_deploy.sh` and `backend/tests/memory`
both have a mode where a check can be unrunnable in the current environment.
Before T-23, both degraded silently -- the exit code (and, for the memory
suite, the visible summary) looked the same whether or not the hard part
actually ran. This doc plus the T-23 code changes fix that: an unrunnable
check is now either a hard failure or a loudly-labeled non-pass, never a
silent pass. Read this before trusting either gate's exit code.

## `scripts/smoke_deploy.sh`

Three checks, run in order against `$1` / `$SMOKE_URL` / the production URL:

| # | Check | What it proves | Credentials / tools required |
|---|-------|-----------------|-------------------------------|
| a | `GET /healthz` returns 200 | the web process is up and answering | none (plain `curl`) |
| b | `POST /api/token` returns a decodable JWT-shaped token with a real `video.roomJoin` grant for the requested identity, and a `ws*` URL | the web process can actually mint usable LiveKit tokens, not just return 200 | none (`curl` + `python3` to decode; `python3` is required -- see below) |
| c | worker liveness via `flyctl machine list -a <app> -j` | at least one `worker` process-group machine is `state=started` **and** has no `exit` event in the last `$SMOKE_WORKER_STABLE_WINDOW_SECONDS` seconds (default 300) **and** any Fly-configured health checks on it are passing | `flyctl` installed AND authenticated (`flyctl auth login`) against the target Fly org; `python3` to parse the JSON |

**As of T-23, there is no "SKIP" outcome any more.** Every condition that
used to print `SKIP: ...` and leave the exit code untouched (`flyctl` not
installed, `flyctl` unauthenticated, unparseable `flyctl` output, `python3`
missing) now calls the script's `fail()` path and makes the exit code
non-zero. **A green run (`exit 0`) means all three checks above actually ran
and actually passed** -- specifically, it means whoever ran it had a
`flyctl` authenticated against the target app. There is no bypass flag; if
you only want to check the web endpoints, that is a different, smaller
script, not this gate weakened.

**Corollary for local/offline use:** on any machine where `flyctl auth
whoami` fails (this repo's dev environment is exactly such a machine --
`flyctl` is installed but unauthenticated: `Error: no access token
available.`), `scripts/smoke_deploy.sh` against the real target URL will
**always** exit non-zero at check (c), by design. That is not a bug in the
script; it is the fix. To get a real, meaningful green run you need
`flyctl auth login` on that machine first.

### Why "worker machine state is `started`" is not the SERVING signal

fly.toml's `[[vm]]` block for the worker process group documents a real
incident: the worker OOM-crashed on boot and restarted in a loop, and
`flyctl status` kept reporting `started` because the machine happened to be
sampled mid-loop, moments after a restart. A point-in-time state check
cannot distinguish "just started and about to crash again" from "been up
for hours." Check (c) instead requires that the machine's own Fly Machines
API event history show **no `exit` event in the stability window** -- a
crash-looping machine's event history is exactly what a bare state snapshot
hides. `SMOKE_WORKER_STABLE_WINDOW_SECONDS` (default 300s / 5 minutes) is
overridable; a smoke test run within that window of a *legitimate* fresh
deploy will also, correctly, not yet be able to call the worker "serving" --
"just started" and "stably serving" are different claims, and the script
does not conflate them. Wait out the window (or lower it, understanding the
tradeoff) before smoke-testing right after a deploy.

**Caveat, stated plainly:** the exact JSON field names checked
(`events[].type == "exit"`, `events[].timestamp`, `checks[].status`) follow
Fly's published Machines API machine-resource schema. They are **not**
verified against a live authenticated `flyctl` in this environment -- this
machine's `flyctl` has no access token, so no real deployment JSON was ever
seen. What *is* verified against the real, installed `flyctl v0.4.45`
binary is the **command shape**: `flyctl machine list -a <app> -j`
argument-parses and reaches the network call (confirmed via `--help` and by
running it unauthenticated, which fails on auth, not on argument parsing);
`flyctl machine status <id> --json` does **not** exist in this flyctl
version, which is why `machine list` is used instead of a per-machine
status call. Re-verify the JSON field assumption against a real deployment
the first time `flyctl auth login` succeeds in this environment, and update
this note.

### Testing the script's own exit-code logic

`scripts/tests/run_smoke_exit_code_tests.sh` puts a stub `flyctl` (and a
stub `curl`, so checks a/b are a constant, deterministic pass) earlier on
`PATH` and drives `scripts/smoke_deploy.sh` through four scripted
responses, asserting the exit code each time:

| Case | flyctl behavior | Expected exit code |
|------|------------------|---------------------|
| `no_credentials` | prints the real observed "no access token available... flyctl auth login" error, exits 1 | non-zero |
| `healthy_deployment` | one worker machine, `state=started`, no `exit` events | zero |
| `crash_looping_worker` | one worker machine, `state=started`, but an `exit` event 5s ago | non-zero |
| `worker_absent` | no `worker`-group machine in the list at all | non-zero |

This is a test of `smoke_deploy.sh`'s own control flow given a known,
scripted `flyctl` response -- it is **not** a live smoke test and does not
prove anything about a real Fly deployment. Run it with:

```
bash scripts/tests/run_smoke_exit_code_tests.sh
```

## `backend/tests/memory`

Two layers (see `test_contract.py` / `test_integration.py` docstrings):

| Layer | Requires | What a pass proves |
|-------|----------|----------------------|
| Contract/unit (`test_contract.py`) | nothing -- runs offline against in-process fakes | the code's logic is internally consistent; proves nothing about mem0 or pgvector actually working |
| Integration (`test_integration.py`) | a reachable Postgres+pgvector at `$JARVIS_TEST_DATABASE_URL` / `$DATABASE_URL` / `postgresql://jarvis:jarvis_dev_local_only@localhost:55432/jarvis` (`cd backend && docker compose up -d`) | facts actually round-trip through mem0 + pgvector, including cross-session recall and per-user isolation |

`require_db` (in `conftest.py`) resolves that URL and tries to connect. What
happens if it can't reach it depends on one env var:

- **Default** (`$JARVIS_MEMORY_TESTS_REQUIRE_DB` unset): the integration
  tests are **skipped**, not failed -- CI runs the whole suite with no
  Postgres present (SPEC C7 forbids adding CI services), so a hard failure
  there would break every build. But the skip is loud: `pytest_terminal_summary`
  in `conftest.py` always prints (independent of `-q`/`-v`) a banner naming
  which suite was skipped and stating plainly that mem0/pgvector were not
  exercised. **A green `pytest tests/memory -q` on a machine with no
  Postgres proves only that the contract/unit tests passed against
  in-process fakes -- it proves nothing about mem0 or pgvector actually
  working, and the terminal output says so explicitly; it is never silent
  about it.**
- **`$JARVIS_MEMORY_TESTS_REQUIRE_DB=1`** (or `true`/`yes`/`on`): an
  unreachable database is a **hard `pytest.fail()`**, not a skip. Set this
  in any context where "no Postgres" should be a red build, not a quiet
  skip -- e.g. a deploy gate or a CI job that does provision Postgres. It is
  intentionally **not** the default, because the default is what makes this
  ticket's own verify command (`cd backend && uv run pytest tests/memory -q`)
  closable on a machine that has no Postgres at all; making the skip a hard
  failure unconditionally would make that verify command permanently
  red on such a machine, punishing the wrong thing.

**On this development machine, Postgres IS reachable** (`docker ps` shows
`jarvis-memory-postgres` on `localhost:55432`), so `cd backend && uv run
pytest tests/memory -q` here runs the real 24 tests (contract + integration)
against real pgvector, not the skip path. The skip / loud-banner / hard-fail
behavior above was additionally verified by hand by pointing
`$JARVIS_TEST_DATABASE_URL` at an unreachable port.

### Summary: what does a green run of each command actually prove?

- `bash scripts/smoke_deploy.sh <url>` exits 0 **only** if `flyctl` was
  authenticated against the target app and the worker was confirmed
  serving (not just present) -- with T-23, there is no configuration in
  which it exits 0 without that.
- `cd backend && uv run pytest tests/memory -q` exits 0 either because (a)
  Postgres was reachable and all 24 contract+integration tests really
  passed, or (b) Postgres was unreachable, `$JARVIS_MEMORY_TESTS_REQUIRE_DB`
  was not set, and only the contract/unit tests ran -- and in case (b) the
  terminal output unmistakably says so in a labeled "MEMORY INTEGRATION
  SUITE SKIPPED" banner. Set `$JARVIS_MEMORY_TESTS_REQUIRE_DB=1` wherever a
  case-(b) green should instead be red.
