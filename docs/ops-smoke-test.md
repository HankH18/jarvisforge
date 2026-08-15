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

**Caveat, updated 2026-08-14 (T-32):** the exact JSON field names checked
(`events[].type == "exit"`, `events[].timestamp`, `checks[].status`) follow
Fly's published Machines API machine-resource schema, and this has now been
**re-verified against a live authenticated `flyctl`** (`flyctl machine list
-a jarvisforge -j`, flyctl v0.4.83, on the real `jarvisforge` app): the real
payload matches every field-shape assumption the script makes -- flat
per-machine objects with top-level `id`/`state`/`config`/`events`/`checks`,
`checks: null` (not `[]`) when the process group has no Fly-configured
check, `events[]` entries carrying `type`/`timestamp` (millis) exactly as
read. Only `"start"`/`"launch"`/`"update"` event types were observed live
(no crash occurred during capture), so the `"exit"`-type branch itself
remains verified against Fly's own published Machines event vocabulary
rather than an observed live crash. Previously this caveat said the shape
had never been checked against a live authenticated `flyctl` at all --
that has been superseded; see "Capturing a fresh live fixture" below for
how to re-verify or refresh this note against a later deploy.

### T-32: the worker check now fails closed on shape drift, not just on absence

An audit (2026-08-14) found that the parser above had an actual fail-OPEN
bug, not just an unverified assumption: an `"exit"` event whose `timestamp`
was missing or unparseable was silently skipped rather than treated as a
failure -- exactly the signal this check exists to catch (a recent crash)
could vanish behind a green verdict. Two adjacent wrong shapes (`"events"`
as something other than a list; a non-dict entry inside `events[]`) crashed
the embedded parser outright, and the shell's catch-all converted that
crash into an unhelpful, unnamed `"no parseable verdict"` failure instead
of naming what was actually wrong. All three are now explicit, named,
fail-closed branches -- a machine with any of these shapes is never counted
as SERVING, and the printed reason always says which shape problem was hit
(e.g. `UNKNOWN: exit event timestamp unparseable (...)`,
`UNKNOWN: events field is not a list (got dict)`,
`UNKNOWN: events[] contains a non-dict entry (str)`). A machine entry with
no `state` field at all is unaffected and continues to fail exactly as
before (it was already correctly excluded from "started").

### Testing the script's own exit-code logic

`scripts/tests/run_smoke_exit_code_tests.sh` puts a stub `flyctl` (and a
stub `curl`, so checks a/b are a constant, deterministic pass) earlier on
`PATH` and drives `scripts/smoke_deploy.sh` through scripted responses,
asserting the exit code each time (and, since T-32, a distinct named-reason
substring in the output for the wrong-shape cases -- a bare exit code
cannot tell "correctly detected and named" apart from "crashed and got
caught by the generic catch-all", which is exactly the bug T-32 closed):

| Case | flyctl behavior | Expected exit code |
|------|------------------|---------------------|
| `no_credentials` | prints the real observed "no access token available... flyctl auth login" error, exits 1 | non-zero |
| `healthy_deployment` | one worker machine, `state=started`, no `exit` events | zero |
| `crash_looping_worker` | one worker machine, `state=started`, but an `exit` event 5s ago | non-zero |
| `worker_absent` | no `worker`-group machine in the list at all | non-zero |
| `exit_event_garbage_timestamp` (T-32) | `exit` event with a non-numeric `timestamp` | non-zero, named `UNKNOWN: exit event timestamp unparseable` -- **the inversion case**: pre-T-32 this exited 0 (silently healthy); see the T-32 completion report for the concrete before/after |
| `exit_event_missing_timestamp` (T-32) | `exit` event with the `timestamp` key absent | non-zero, named `UNKNOWN: exit event timestamp unparseable` |
| `events_as_dict` (T-32) | `"events"` is a JSON object, not a list | non-zero, named `UNKNOWN: events field is not a list` (never the generic `no parseable verdict`) |
| `events_non_dict_entry` (T-32) | `events[]` contains a bare string, not an object | non-zero, named `UNKNOWN: events[] contains a non-dict entry` (never the generic `no parseable verdict`) |
| `machine_missing_state` (T-32) | machine entry has no `state` key at all | non-zero -- pins down that this pre-existing, unchanged behavior was not broken by the hardening above |
| `live_shape_snapshot` (T-32) | a real, sanitized snapshot captured from a live authenticated flyctl (2026-08-14) | zero -- proves the hardening above does not false-alarm on a real, legitimate payload |

This is a test of `smoke_deploy.sh`'s own control flow given a known,
scripted `flyctl` response -- it is **not** a live smoke test and does not
prove anything about a real Fly deployment (beyond what the `live_shape_snapshot`
case's one committed real capture already showed). Run it with:

```
bash scripts/tests/run_smoke_exit_code_tests.sh
```

### Capturing a fresh live fixture (do this the first time you have `flyctl auth login`'d in a new environment)

`scripts/tests/run_smoke_exit_code_tests.sh` will pick up ONE more,
optional case -- `live_fixture_local` -- whenever a freshly captured
fixture file exists on disk. It is never committed (`flyctl auth` is a
human-local credential, not something CI or a fresh checkout has), so its
absence is normal: the harness prints an `INFO:` line and simply runs
without it. To capture (and refresh) one, in one step:

```
mkdir -p scripts/tests/fixtures
flyctl machine list -a jarvisforge -j > scripts/tests/fixtures/live_flyctl_machine_list.json.local
bash scripts/tests/run_smoke_exit_code_tests.sh
```

The `.json.local` suffix matches this repo's existing `*.local` `.gitignore`
pattern, so the raw capture (real machine IDs, private IPs, image digests)
never gets committed by accident. The harness does not assert a fixed exit
code for this case, because a fresh capture's true health state is whatever
production actually was at capture time -- it instead asserts that the real
payload never falls through to the generic, unnamed `no parseable verdict`
catch-all, which would mean an unanticipated real-world shape reached the
parser undetected (the exact regression class T-32 exists to close).

If you want that capture to become a **permanent**, committed regression
case (not just a local, gitignored one-off), sanitize it -- strip
`private_ip`, `instance_id`, `image_ref`, and real machine `id`/`name`
values, keeping only the fields the parser reads (`id`, `state`,
`config.metadata.fly_process_group`, `checks`, `events[].type`/`timestamp`)
-- and update the `live_shape_snapshot` case's embedded JSON in
`scripts/tests/run_smoke_exit_code_tests.sh` with it, along with the
"re-verified" date/flyctl-version note above.

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
  which it exits 0 without that. With T-32, "confirmed serving" also fails
  closed on shape drift: a machine whose event history cannot be safely
  read (missing/unparseable exit timestamp, `events` not a list, a
  non-dict entry inside it) is never counted toward that confirmation.
- `cd backend && uv run pytest tests/memory -q` exits 0 either because (a)
  Postgres was reachable and all 24 contract+integration tests really
  passed, or (b) Postgres was unreachable, `$JARVIS_MEMORY_TESTS_REQUIRE_DB`
  was not set, and only the contract/unit tests ran -- and in case (b) the
  terminal output unmistakably says so in a labeled "MEMORY INTEGRATION
  SUITE SKIPPED" banner. Set `$JARVIS_MEMORY_TESTS_REQUIRE_DB=1` wherever a
  case-(b) green should instead be red.
