# Task Graph (GENERATED from tickets.json — do not hand-edit)

### T-0: Repo skeleton, test harness, lint, light CI green on empty suite  `[resolved]`
- **Objective**: Bootstrap so every downstream verify command is runnable: Python backend (uv, pytest, ruff), Vite React frontend (vitest), scripts dir, light GHA workflow, .env.example.
- **Acceptance**: 1) backend/ initialized with uv; pytest and ruff configured; empty suite passes 2) frontend/ Vite React TS scaffold builds; vitest configured with one trivial passing test 3) .github/workflows/ci.yml: single job, ruff + pytest, pull_request trigger only 4) .env.example lists every env var named in DESIGN.md; no secrets committed
- **Verify**: `cd backend && uv run pytest -q && uv run ruff check src && cd ../frontend && npm run -s build`
- **Scope**: `backend/**, frontend/**, scripts/**, .github/**, Makefile, README.md, .env.example, .gitignore`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: No app logic; no provider SDK calls; no Docker/Fly config

### T-1: Voice loop works end-to-end: STT -> LLM -> TTS on LiveKit with state events  `[resolved]`
- **Objective**: LiveKit Agents worker joins a room, streams Deepgram STT in, a single fast model replies, Cartesia TTS streams out; FastAPI token endpoint; agent_state and transcript data-channel events per DESIGN.
- **Acceptance**: 1) Worker registers with LiveKit and completes a synthetic audio turn in tests with mocked STT/TTS providers 2) POST /api/token returns a valid LiveKit token per the pinned request/response shape 3) agent_state transitions listening->thinking->speaking emitted on the data channel 4) TTS provider selected by env var; Cartesia default, Aura-2 fallback both wired 5) Per-turn latency JSONL logging in place (turn_end_ts, first_audio_ts)
- **Verify**: `cd backend && uv run pytest tests/pipeline -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/pipeline/**, backend/src/jarvis/server/**, backend/tests/pipeline/**`
- **Depends on**: T-0 · **parallel_safe**: false
- **Non-goals**: No tools, no memory, no grounding policy, no frontend

### T-2: Grounded tool-calling core: routing, ANSWER_POLICY, refusal, self-manifest  `[resolved]`
- **Objective**: Dual-model routing (CONVO_MODEL always-on, TOOL_MODEL escalation for heavy tools), ToolResult type, tool registry, synthesize_grounded with REFUSAL behavior, capabilities.yaml + self_manifest tool (R5).
- **Acceptance**: 1) ToolResult, REFUSAL, synthesize_grounded implemented exactly as pinned in DESIGN.md 2) Routing escalates to TOOL_MODEL only for tools tagged heavy 3) Given empty/irrelevant tool results, answer starts with REFUSAL (unit-tested on fixture transcripts) 4) Capability questions answered solely from capabilities.yaml (unit-tested)
- **Verify**: `cd backend && uv run pytest tests/grounding -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/llm/**, backend/src/jarvis/tools/**, backend/src/jarvis/capabilities.yaml, backend/tests/grounding/**`
- **Depends on**: T-1 · **parallel_safe**: false
- **Non-goals**: No concrete external tools (weather/github land in T-3/T-4)

### T-3: Weather answers grounded in live Open-Meteo data  `[resolved]`
- **Objective**: get_weather tool (geocode + forecast, keyless Open-Meteo) returning ToolResult; answers cite recency when asked (R8).
- **Acceptance**: 1) get_weather returns pinned ToolResult shape for now/today/week using recorded API fixtures 2) Unknown location or API error yields ToolResult.error and a REFUSAL-path answer, not a guess 3) fetched_at populated; recency question answered from it (unit-tested)
- **Verify**: `cd backend && uv run pytest tests/tools/test_weather.py -q`
- **Scope**: `backend/src/jarvis/tools/weather/**, backend/tests/tools/test_weather.py, backend/tests/tools/fixtures/weather/**`
- **Depends on**: T-2 · **parallel_safe**: true
- **Non-goals**: No paid weather providers; no caching layer

### T-4: GitHub Q&A grounded in live repo data with graceful oversize degradation  `[resolved]`
- **Objective**: load_repo_digest (gitingest, cached by (repo, head_sha), 15-min TTL, token budget) and github_query (githubkit GraphQL: open_prs, open_issues, pr_comments, last_merges, repo_meta), both heavy-tagged (R7).
- **Acceptance**: 1) Both tools return pinned ToolResult shapes against recorded fixtures for a pinned public repo snapshot 2) Digest exceeding token_budget triggers targeted-query degradation, never truncated-content answering (unit-tested) 3) Cache hit on unchanged head_sha within TTL (unit-tested) 4) Invalid/private/nonexistent URL yields ToolResult.error -> REFUSAL path
- **Verify**: `cd backend && uv run pytest tests/tools/test_github.py -q`
- **Scope**: `backend/src/jarvis/tools/github/**, backend/tests/tools/test_github.py, backend/tests/tools/fixtures/github/**`
- **Depends on**: T-2 · **parallel_safe**: true
- **Non-goals**: No embedding index; no write actions on repos (PR-opening is T-11)

### T-5: Cross-session memory round-trips through self-hosted mem0 on pgvector  `[resolved]`
- **Objective**: MemoryStore protocol implemented on mem0 OSS + Postgres/pgvector (docker-compose for local dev); session turns persisted; search surfaces prior-session facts as ToolResults; preferences table created (R3).
- **Acceptance**: 1) Round-trip test: fact stored under user U in session A is returned by search(U, ...) in a new session B 2) search results are ToolResult source=memory:mem0 feeding synthesize_grounded 3) Isolation primitive proven: search(U2) never returns U1 rows (full multi-user lands in T-10) 4) preferences table migrated; get/add_preference round-trip
- **Verify**: `cd backend && uv run pytest tests/memory -q`
- **Scope**: `backend/src/jarvis/memory/**, backend/tests/memory/**, backend/docker-compose.yml`
- **Depends on**: T-2 · **parallel_safe**: true
- **Non-goals**: No Clerk/auth; no preference voice-capture UX (T-10)

### T-6: Cadence and interruption feel natural: fillers, barge-in, stop phrase  `[resolved]`
- **Objective**: Filler bank pre-synthesized; >500ms heavy-tool turns get a spoken acknowledgment within 300ms; any user speech pauses playback; stop phrases cancel the turn (R2, R4). Exploratory tuning: hit the boundaries, don't gold-plate.
- **Acceptance**: 1) Filler gating unit-tested: stubbed tool latency >500ms triggers filler, <500ms does not 2) Filler assets generated by scripts/build_fillers.py into assets/fillers/ 3) Stop-phrase detection cancels turn; barge-in pauses playback (state-machine unit tests) 4) No-immediate-repeat filler selection unit-tested
- **Verify**: `cd backend && uv run pytest tests/cadence -q`
- **Scope**: `backend/src/jarvis/cadence/**, backend/tests/cadence/**, backend/assets/fillers/**, scripts/build_fillers.py`
- **Depends on**: T-2 · **parallel_safe**: true
- **Non-goals**: No semantic turn-detection model swaps; LiveKit defaults + tuning only

### T-7: Grounding eval harness passes with zero fabrications on approved golden set  `[resolved]`
- **Objective**: evals/golden.jsonl (>=30 cases: grounded answers, forced refusals, memory recalls) + evals.run executing cases against synthesize_grounded with fixture tools; Hank approves the golden set (R6a external ground truth).
- **Acceptance**: 1) golden.jsonl has >=30 cases in the pinned schema covering all three case types 2) Hank has reviewed and approved golden.jsonl (approval noted in the file header comment) - REQUIRED before close 3) evals.run reports 0 fabrications and 0 wrong-refusals; nonzero exits nonzero
- **Verify**: `cd backend && uv run python -m evals.run --golden evals/golden.jsonl --max-fabrications 0 --require-approved`
- **Scope**: `backend/evals/**`
- **Depends on**: T-3, T-4, T-5 · **parallel_safe**: false
- **Non-goals**: No live-API eval cases (fixtures only); no CI wiring (local merge gate by design)

### T-8: Polished SPA: join, converse, see live transcript and agent state  `[resolved]`
- **Objective**: React/Vite client: token fetch, room join, mic controls, live transcript, listening/thinking/working/speaking indicators. Polished per frontend-design guidance; UI simple, UX sharp.
- **Acceptance**: 1) Client fetches /api/token with pinned shape and joins the room (mocked in tests) 2) agent_state and transcript events render (component tests) 3) Production build passes; no Next.js anywhere in the dependency tree
- **Verify**: `cd frontend && npm run -s build && npm run -s test -- --run`
- **Scope**: `frontend/**`
- **Depends on**: T-1 · **parallel_safe**: true
- **Non-goals**: No auth UI (T-10), no wake word (T-12)

### T-9: Deployed on Fly.io: web + worker live, smoke passing, MVP demo-able  `[resolved]`
- **Objective**: Single Fly app, two process groups (web=FastAPI+static SPA, worker=agent); Fly Postgres attached; secrets set; smoke script green against the deployed URL; latency report from deployed instance meets R1.
- **Acceptance**: 1) fly.toml defines web + worker process groups; backend Dockerfile builds 2) scripts/smoke_deploy.sh: health endpoint 200, token endpoint returns valid shape, worker registered 3) scripts/latency_report.py over >=50 deployed turns shows p95 <= 800ms (R1) - report committed as docs/latency-report.md
- **Verify**: `bash scripts/smoke_deploy.sh`
- **Scope**: `fly.toml, backend/Dockerfile, scripts/**, .github/workflows/**, docs/latency-report.md`
- **Depends on**: T-6, T-7, T-8 · **parallel_safe**: false
- **Non-goals**: No autoscaling config; no custom domains; no Vercel

### T-10: Bonus: multi-user with Clerk - isolated memory, honored preferences, 10 concurrent  `[queue]`
- **Objective**: Clerk sign-in in SPA; /api/token requires Clerk JWT and derives identity server-side; mem0 namespacing by Clerk user id; voice-captured standing preferences honored across sessions; 10-session load smoke (R9).
- **Acceptance**: 1) Unauthenticated /api/token returns 401; identity comes from verified JWT (client identity ignored) 2) Isolation test: user A's stored fact absent from user B's recall 3) Preference stated by voice in session 1 is honored in session 2 (test via prefs injection path) 4) scripts/load_smoke.sh sustains 10 concurrent sessions without errors
- **Verify**: `(cd backend && uv run pytest tests/multiuser -q) && bash scripts/load_smoke.sh 10`
- **Scope**: `backend/src/jarvis/auth/**, backend/src/jarvis/server/**, backend/src/jarvis/memory/**, backend/tests/multiuser/**, frontend/src/auth/**, frontend/src/App.tsx, scripts/load_smoke.sh`
- **Depends on**: T-9 · **parallel_safe**: false
- **Non-goals**: No roles/orgs; no billing; no LiveKit self-hosting even if Cloud free-tier concurrency caps bite - flag to human instead

### T-11: Bonus: 'fix issue #N' opens a real PR with audible progress  `[resolved]`
- **Objective**: run_fix_job: background Claude Agent SDK run in isolated workdir with scoped GITHUB_TOKEN against a user-controlled test repo; announces start/progress/PR-opened via data channel + TTS; opens PR, never merges (R10).
- **Acceptance**: 1) Job orchestration unit-tested with a mocked agent (states: started -> progress -> pr_opened|failed) 2) Announcements emitted at each state transition 3) One recorded live run against the designated test repo produced an open PR (PR URL committed to docs/agent-pr-demo.md) 4) No merge or force-push capability in the token scope
- **Verify**: `cd backend && uv run pytest tests/agent_pr -q`
- **Scope**: `backend/src/jarvis/agents_pr/**, backend/tests/agent_pr/**, docs/agent-pr-demo.md`
- **Depends on**: T-9 · **parallel_safe**: false
- **Non-goals**: No arbitrary-repo writes; no auto-merge; no retry queues

### T-12: Bonus: passive mode wakes on 'Jarvis' with zero upstream audio while passive  `[resolved]`
- **Objective**: Porcupine Web SDK with built-in 'Jarvis' keyword; passive/active client state machine; mic audio published to LiveKit only in active state (R11).
- **Acceptance**: 1) State machine unit tests: passive -> wake event -> active -> session end -> passive 2) Track-publish gating tested: no LiveKit audio track published while passive 3) Passive mode is an explicit user toggle, default off
- **Verify**: `cd frontend && npm run -s test -- --run`
- **Scope**: `frontend/src/wake/**, frontend/src/state/**, frontend/src/App.tsx`
- **Depends on**: T-9 · **parallel_safe**: false
- **Non-goals**: No custom wake words; no mobile; no background-tab guarantees

### T-13: Submission package sent: video, docs, zip emailed, checklist green  `[queue]`
- **Objective**: Technical documentation (architecture + AI-usage note), 5-10 min demo video incl. GitHub Q&A against the chosen active mid-size OSS repo, private-repo confirmation, zip emailed to wduffy@ and pmoeckel@frontieraudio.com.
- **Acceptance**: 1) PLACEHOLDER - BLOCKED ON SPEC open question 1: video content checklist finalized from the missing PRD screenshot region before recording 2) docs/TECHNICAL.md covers architecture, stack rationale, and known limits 3) scripts/verify_submission.sh checks: repo private, video file present, TECHNICAL.md present, zip built 4) Zip sent to both addresses; sent-confirmation noted in docs/SUBMISSION.md
- **Verify**: `bash scripts/verify_submission.sh`
- **Scope**: `docs/**, scripts/verify_submission.sh, submission/**`
- **Depends on**: T-9 · **parallel_safe**: false
- **Non-goals**: Do not block on bonuses - submit with whatever is green

### T-14: Wire grounded tool-calling into the voice agent  `[resolved]`
- **Objective**: The tool registry, ANSWER_POLICY, synthesize_grounded and the weather/github/memory/self_manifest tools all exist and pass their tests, but nothing connects them to the LiveKit AgentSession - backend/src/jarvis/pipeline/ contains zero references to the registry. The running product is therefore a plain conversational LLM: asked for London weather it answers 'I don't have access to real-time weather data'. Found against the live deployment after T-9. R6 survives by accident (it refuses rather than fabricating); R3, R7 and R8 are unmet in the actual product.
- **Acceptance**: 1) AgentSession is constructed with the tool registry attached so the model can actually call tools 2) A weather question invokes get_weather and the spoken answer is drawn from ToolResult data, not training knowledge 3) Heavy tools (github_qa) escalate the answer step to $TOOL_MODEL per DESIGN routing; weather/memory/self_manifest stay on $CONVO_MODEL 4) Refusal still holds: a question no tool result supports returns the REFUSAL prefix 5) An integration test drives a turn through the real session wiring and asserts a tool was actually invoked - not a mock standing in for the agent
- **Verify**: `cd backend && uv run pytest tests/pipeline -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/pipeline/**, backend/tests/pipeline/**`
- **Depends on**: T-2, T-3, T-4, T-5 · **parallel_safe**: false
- **Non-goals**: No new tools - wire the ones that already exist; No changes to the pinned ToolResult or synthesize_grounded contracts

### T-15: Wire cadence into the voice agent: fillers, barge-in, stop phrase  `[resolved]`
- **Objective**: T-6 built backend/src/jarvis/cadence/ (FillerGate, TurnController, pre-synthesized Cartesia filler clips) with 69 passing tests, but nothing in backend/src/jarvis/pipeline/ imports it -- the same gap T-14 fixed for tools. R2 (spoken acknowledgment within 300ms when a lookup will take >500ms) and R4 (barge-in pauses playback, stop phrases cancel the turn) are unmet in the running product. Measured tool turns on the deployment take 1.6-14.4s, so the filler is what makes them bearable.
- **Acceptance**: 1) A heavy/slow tool turn plays a pre-synthesized filler clip; a fast turn plays none 2) The agent_state wire vocabulary emits "working" while a heavy tool runs (events.py reserves it for exactly this) 3) User speech during playback pauses it (barge-in); a stop phrase cancels the turn 4) An integration test drives the real session wiring and asserts a filler was played on a slow turn and not on a fast one
- **Verify**: `cd backend && uv run pytest tests/pipeline tests/cadence -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/pipeline/**, backend/src/jarvis/cadence/**, backend/tests/pipeline/**, backend/tests/cadence/**`
- **Depends on**: T-6, T-14 · **parallel_safe**: false
- **Non-goals**: No new filler assets; use the committed Cartesia clips; No semantic turn-detection model swaps

### T-16: Meet R1: cut time-to-first-audio on no-lookup turns below 800ms p95  `[resolved]`
- **Objective**: Measured over 55 real deployed turns, no-lookup replies run p50 821ms / p95 1448ms against R1's 800ms p95 bar (docs/latency-report.md). Close the gap with the cheap levers first: LiveKit preemptive generation, TTS time-to-first-byte settings, and removing any avoidable round-trip on turns where no tool fires. Also fix the observability gaps found while measuring -- tool_ms is logged as 0.0 even when a tool fires, and `model` always logs CONVO_MODEL even on escalated turns -- so the report can tell lookup from no-lookup without the driver.
- **Acceptance**: 1) tool_ms is populated with real tool time, and `model` reflects the model that actually answered the turn 2) A fresh >=50-turn deployed run is captured and docs/latency-report.md regenerated from it 3) p95 time-to-first-audio on no-lookup turns is <= 800ms -- or, if the cascaded pipeline cannot reach it, the report states the achieved figure and the specific remaining bottleneck with evidence rather than quietly missing the bar
- **Verify**: `cd backend && uv run pytest tests/pipeline -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/pipeline/**, backend/tests/pipeline/**, scripts/**, docs/latency-report.md, fly.toml`
- **Depends on**: T-14 · **parallel_safe**: false
- **Non-goals**: No speech-to-speech model swap (SPEC rejects it); Do not weaken R1's definition to make it pass

### T-17: A failed filler must not turn a successful lookup into a refusal  `[resolved]`
- **Objective**: FillerGate.guard()'s finally block suppresses only CancelledError, then awaits the timer task. If the filler coroutine raised (TTS hiccup, audio device, Cartesia stream), that exception replaces the tool's return value. tools_wiring catches it and builds ToolResult(data={}, error=...), which usable_results() discards, so has_supporting_evidence() is False and synthesize_grounded returns REFUSAL: the user hears "I don't know" about a fact the system successfully retrieved. Dormant until T-15 put guard() on the live path; with weather p50 2663ms and github p50 1648ms every heavy turn now crosses FILLER_THRESHOLD_MS, so the filler path runs constantly. Reproduced against committed code. gate.decide() is also dead here: zero call sites in src/, duplicating the live timer rule so a future fix can land in one copy and not the other.
- **Acceptance**: 1) A filler player that raises leaves guard()'s return value intact: the awaited tool result is returned unchanged and the exception is logged, never propagated 2) A test drives guard() with a player that raises and asserts the tool's real value is returned - the existing tests all use a player that always succeeds, so this direction is currently unexercised 3) An integration test through tools_wiring asserts a heavy tool whose filler fails still yields a usable ToolResult (non-empty data, error None) and therefore does NOT produce REFUSAL 4) Cancellation semantics are unchanged: cancelling guard() still cancels the timer and still raises CancelledError to the caller, and the existing no-leak tests still pass unmodified 5) gate.decide() is either deleted with its export, or called by _speak_after_delay so one rule governs both paths - not left as a second unreferenced copy
- **Verify**: `cd backend && uv run pytest tests/cadence tests/pipeline -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/cadence/**, backend/tests/cadence/**, backend/src/jarvis/pipeline/tools_wiring.py, backend/tests/pipeline/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: No change to the pinned FILLER_THRESHOLD_MS / FILLER_START_DEADLINE_MS values; No change to the at-most-one-filler-per-guard-call contract; Does not weaken the broad handler except in tools_wiring - a genuinely broken handler must still degrade to an error ToolResult

### T-18: Size the synthesis token budget for an adaptive-thinking TOOL_MODEL  `[resolved]`
- **Objective**: llm/synthesis.py pins _MAX_TOKENS = 1024 and passes no thinking parameter. On claude-sonnet-4-6 omitting thinking meant thinking off, so all 1024 tokens went to the answer. TOOL_MODEL is now claude-sonnet-5, where omitting thinking runs adaptive thinking by default and max_tokens caps thinking plus response text together. Every heavy-tool turn escalates to TOOL_MODEL per route_model(), so grounded answers on exactly the turns that carry real tool data can truncate mid-sentence. A truncated answer that loses its required fact scores missing_include in the eval, so this surfaces as an unexplained grounding failure rather than a token-budget one.
- **Acceptance**: 1) _MAX_TOKENS gives an adaptive-thinking model room for thinking plus a complete grounded answer; the chosen value and its reasoning are recorded in a comment naming the adaptive-thinking interaction 2) A test asserts synthesize_grounded's request carries a max_tokens large enough that a representative grounded answer plus thinking fits - the fake ModelClient records the value, so this needs no network 3) The eval is re-run and docs record that no case regressed to missing_include for truncation reasons 4) If a thinking parameter is passed instead of raising the budget, it is verified valid for BOTH $CONVO_MODEL and $TOOL_MODEL, since one complete() serves both
- **Verify**: `cd backend && uv run pytest tests/grounding -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/llm/**, backend/tests/grounding/**`
- **Depends on**: none · **parallel_safe**: true
- **Non-goals**: No hardcoded model ids - routing stays $CONVO_MODEL / $TOOL_MODEL only (SPEC C3); No change to the pinned synthesize_grounded signature or ToolResult shape

### T-19: Bind the grounded negative-fact rule with a test and an unambiguous golden case  `[queue]`
- **Objective**: fd5b88b added the rule that a fact the data establishes NEGATIVELY (absent, zero, a listed limitation) is a supported ANSWER rather than a refusal. Nothing binds it: tests/grounding/test_policy.py contains four substring assertions that pass identically before and after the change, the suite count did not move, and no golden case exercises the behaviour. Separately, golden case G-004 asks the chance of rain on 2026-08-18 with must_include ['27'], but '27' matches two distinct facts in its own fixture - precipitation_probability_max[5] = 27 (the correct answer) and temperature_2m_max[4] = 27.0 (a different field on a different day) - so an answer citing the wrong field scores pass.
- **Acceptance**: 1) A test asserts ANSWER_POLICY carries the negative-fact rule in a way that fails if the paragraph is removed - not a bare substring check that any prose would satisfy 2) At least one golden case has kind grounded / expect answer over a fixture whose data answers NEGATIVELY (zero, absent, or an explicit limitation), with must_include pinning the negative answer and must_not_include pinning the REFUSAL prefix 3) G-004 is disambiguated so exactly one fact in its fixture can satisfy must_include - either by making the colliding value unique or by pinning a longer, unambiguous substring 4) A scripted check over golden.jsonl asserts no case's must_include substring appears more than once across distinct fields of that case's own fixture, so this class of ambiguity cannot silently return 5) The golden set's _meta approved_by / approved_at are NOT set by this ticket - changing the set means it needs Hank's review again
- **Verify**: `cd backend && uv run pytest tests/grounding -q && uv run python -m evals.run --golden evals/golden.jsonl --max-fabrications 0 --client echo || true`
- **Scope**: `backend/src/jarvis/llm/policy.py, backend/tests/grounding/**, backend/evals/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: Does not sign off the golden set on a human's behalf; No change to the REFUSAL literal or the pinned scoring verdict names

### T-20: The scope guard covers every write path, and stops guarding the plan away  `[queue]`
- **Objective**: settings.json registers scope_guard.sh under matcher 'Edit|Write' only, so any write performed through Bash - sed -i, a heredoc, cp, git checkout - bypasses the ticket boundary entirely. scope_guard.sh line 11 then exempts .claude/* and docs/*, which is precisely docs/tickets.json, SPEC.md and DESIGN.md: the files protocol rule 1 exists to protect are the ones the guard waves through. Its path normalisation also leaves an absolute path unchanged when the target is outside CLAUDE_PROJECT_DIR, so every write to a scratch or temp directory is denied while a ticket is claimed. Finally the jq glob to regex translation appends '$' but no '^', so test() searches rather than full-matches and a path merely containing a scope prefix passes.
- **Acceptance**: 1) The guard is reached for Bash-mediated writes, or the protocol documents in CLAUDE.md that Bash is deliberately outside the boundary and why - silent bypass is not an acceptable resting state 2) docs/ is no longer blanket-exempt: an edit to docs/tickets.json, docs/SPEC.md or docs/DESIGN.md is denied unless the active ticket's scope lists it 3) A path outside CLAUDE_PROJECT_DIR is allowed rather than denied - the guard constrains work inside the repo, it does not own the filesystem 4) The generated regex is anchored at both ends, so 'vendor/backend/src/jarvis/cadence/x.py' does not satisfy a 'backend/src/jarvis/cadence/**' scope 5) A test script exercises the hook directly with representative allowed and denied paths for at least three tickets, and runs in CI
- **Verify**: `bash .claude/hooks/tests/run_scope_guard_tests.sh`
- **Scope**: `.claude/hooks/**, .claude/settings.json, CLAUDE.md, .github/workflows/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: Does not loosen any ticket's declared scope to make current work legal; Does not disable the guard as a way of passing its own tests

### T-21: Ticket claims are session-scoped and carry an owner  `[queue]`
- **Objective**: .claude/active-ticket holds a bare ticket id and nothing else, so both guards key off repo-global state with no notion of who claimed it. stop_guard.sh therefore blocks every session in the repo from stopping while any ticket is open - including a read-only observer session that claimed nothing - and offers only finish, revert, or release, none of which a non-owner may safely do. scope_guard.sh has the mirror problem: with a claim active it constrains sessions that do not own it, and with no claim at all it permits everything. Protocol rule 9 contemplates three concurrent agents in worktrees, which neither guard can distinguish.
- **Acceptance**: 1) A claim records an owner identifier alongside the ticket id, written when the ticket is claimed 2) stop_guard.sh blocks only the session that owns the claim; a session that owns no claim stops without being blocked 3) scope_guard.sh applies a ticket's scope only to the owning session 4) Guards behave safely when no claim exists: a missing active-ticket does not silently disable the scope boundary for a session that does hold work 5) A test exercises both guards for owner and non-owner sessions and runs in CI
- **Verify**: `bash .claude/hooks/tests/run_claim_tests.sh`
- **Scope**: `.claude/hooks/**, .claude/settings.json, CLAUDE.md, .github/workflows/**`
- **Depends on**: T-20 · **parallel_safe**: false
- **Non-goals**: No change to the one-ticket-at-a-time rule; Does not introduce a lock server or any external coordination service

### T-22: Evidence binds to the commit it certifies  `[resolved]`
- **Objective**: verify_gate.sh writes a bare epoch timestamp to .claude/evidence/<T-id>.pass and nothing else, so a receipt records when a ticket passed but not what it passed. Reopening a ticket silently invalidates it: T-2.pass is stamped 06:27 while its last change is fd5b88b at 15:37, and T-6.pass is stamped 09:27 while its filler rebuild landed at 22:18. Both receipts now attest to code that no longer exists - T-6's describes macOS-say clips replaced hours ago. T-13's submission checklist consumes this directory, so the submission can report green against superseded work.
- **Acceptance**: 1) A .pass receipt records the HEAD sha it was produced at, alongside the timestamp 2) A check reports any ticket whose last touching commit is newer than its receipt, and names them 3) That check is part of T-13's submission verification, so a stale receipt cannot report green 4) Re-running verify for a reopened ticket refreshes the receipt; the stale receipts for T-2 and T-6 are regenerated or explicitly reported as stale by this ticket's own check
- **Verify**: `bash scripts/tests/run_evidence_tests.sh`
- **Scope**: `scripts/**, .github/workflows/**`
- **Depends on**: none · **parallel_safe**: true
- **Non-goals**: Does not re-run or re-verify other tickets' work as a side effect; Does not delete existing receipts

### T-23: A skipped check is never a passing check  `[resolved]`
- **Objective**: scripts/smoke_deploy.sh degrades to success when its hardest check cannot run: SKIPs do not affect the exit code, so with flyctl unauthenticated the worker-registration check skips and the script exits 0 having verified only the web process - the component the ticket is about goes unchecked while the gate reads green. T-9's own commit describes the adjacent failure, that a crash-looping machine still reports state started, so a machine-state check could not see the OOM. The same shape appears in tests/memory/conftest.py, where require_db calls pytest.skip when Postgres is unreachable: the memory integration suite vanishes with no failure, and a green run means two different things depending on the machine.
- **Acceptance**: 1) smoke_deploy.sh exits non-zero when a check could not be performed against a target that is supposed to be deployed - unrunnable is a failure, not a pass 2) The worker check verifies the worker is serving rather than merely present: a crash-looping or capacity-starved machine does not satisfy it 3) A run with no credentials and a run against a healthy deployment are both covered by a test of the script's own exit-code logic 4) The memory suite's skip is loud: a run where the database was unreachable cannot be reported as a clean pass, and the summary states which suites were skipped 5) docs record which checks require which credentials, so a green local run's meaning is unambiguous
- **Verify**: `(cd backend && uv run pytest tests/memory -q) && bash scripts/tests/run_smoke_exit_code_tests.sh`
- **Scope**: `scripts/**, backend/tests/memory/**, docs/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: Does not require CI to provision Postgres or Fly - SPEC C7 caps CI cost; Does not delete the skip; it makes the skip visible and non-passing

### T-24: Verify commands cover their blast radius, and CI actually runs  `[resolved]`
- **Objective**: Every ticket verify from T-1 onward is scoped to its own subdirectory, so a ticket can go green while breaking a sibling's tests; only T-0 runs the whole suite. The full suite and ruff live in .github/workflows/ci.yml, which triggers on pull_request - and the repo has zero git remotes and one branch, so that workflow has never executed once. The cross-ticket regression gate the project believes it has does not exist in either place.
- **Acceptance**: 1) Closing any ticket runs a check that would fail if the change broke a test outside that ticket's scope 2) That check is cheap enough to run at every ticket boundary and does not violate SPEC C7's CI cost cap 3) The CI workflow is reachable: either a remote and PR flow exist, or the workflow triggers on something this repo actually does, or CLAUDE.md states plainly that CI is decorative and names the local command that replaces it 4) docs/TASKS.md records which verify commands are narrow by design and what covers the gap
- **Verify**: `cd backend && uv run pytest -q && uv run ruff check src && cd ../frontend && npm run -s test -- --run`
- **Scope**: `.github/workflows/**, docs/**, CLAUDE.md, .claude/hooks/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: Does not add CI jobs, matrices, or e2e runs - SPEC C7 is a hard constraint; Does not widen any existing ticket's verify command retroactively

### T-25: Machine-enforce the golden-set human approval gate  `[resolved]`
- **Objective**: T-7's acceptance 2 requires a human's review of evals/golden.jsonl, recorded in the _meta header. verify_gate.sh runs one command - evals.run - which reads the cases and never inspects approved_by, so the ticket's gate is structurally incapable of checking its own acceptance criterion. The project's R6a claim is that the golden set is EXTERNAL ground truth; nothing in the harness distinguishes an externally reviewed set from one signed in place.
- **Acceptance**: 1) evals.run exits NON-ZERO while _meta.approved_by or _meta.approved_at is empty, unless explicitly asked for a draft run 2) The non-zero path cannot be bypassed by a flag, env var, or argument that a build agent would reach for; a draft run is obtained by an explicit, clearly-named draft mode that prints that its result is not evidence 3) Tests cover both directions using a SYNTHETIC approved fixture in tmp_path; the real evals/golden.jsonl is never modified by the test 4) docs/tickets.json T-7 verify is updated to assert approval, so the gate and the acceptance criterion agree 5) This ticket never sets approved_by or approved_at, and never edits evals/golden.jsonl - it exists to protect the gate, not to pass through it
- **Verify**: `cd backend && uv run pytest tests/grounding -q`
- **Scope**: `backend/evals/**, backend/tests/grounding/**, docs/tickets.json`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: Does not approve the golden set; Does not change any case's content or the scoring verdicts

### T-26: Package-safe asset resolution and runtime-output hygiene  `[queue]`
- **Objective**: cadence/fillers.py resolves ASSETS_DIR as Path(__file__).resolve().parents[3] / 'assets' / 'fillers', which is correct only when the package is run from the source tree; installed as a wheel the parent chain differs and the filler bank cannot load, so T-15's cadence silently degrades in exactly the container T-9 deploys. Separately backend/var/ has no ignore rule and backend/var/latency.jsonl is now tracked, so per-run telemetry churns in every diff and would ride into T-13's submission zip. The same gap covered .gstack/ earlier: .gitignore has rules for .venv/, dist/, .env*, .claude/evidence/ and .DS_Store, but none for runtime output.
- **Acceptance**: 1) Filler assets resolve correctly both from a source checkout and from an installed distribution; a test asserts resolution does not depend on the parent-directory depth of __file__ 2) The deployed image is shown to load the filler bank - a container or import-path test, not only a local run 3) backend/var/ and other runtime output directories are gitignored, and backend/var/latency.jsonl is untracked without losing the committed measurement history referenced by docs/latency-report.md 4) docs state where deployed latency records are collected from, since latency_report.py's default input is a local repo path that deployed runs never write to
- **Verify**: `cd backend && uv run pytest tests/cadence -q && uv run ruff check src`
- **Scope**: `backend/src/jarvis/cadence/**, backend/tests/cadence/**, .gitignore, scripts/**, docs/**`
- **Depends on**: none · **parallel_safe**: true
- **Non-goals**: No change to the pinned filler manifest schema or the phrase list; Does not rebuild the filler audio

### T-27: Assert the agent-state wire vocabulary cannot drift between backend and SPA  `[queue]`
- **Objective**: The pinned wire vocabulary is declared twice and nothing asserts the two agree: backend/src/jarvis/pipeline/events.py defines WireAgentState / _WIRE_STATES, frontend/src/lib/types.ts defines AgentState / AGENT_STATES. Both currently list the same four states, so the drift is latent rather than present - but adding a fifth state on one side is a one-line change that no test would catch, and the SPA's parser returns null for an unknown state, so the symptom would be a silently missing UI transition rather than an error. This matters more since 552d769: for the whole build before it, publish_data was never awaited, so the frontend consumed a channel that produced nothing and no test on either side would have noticed. Both sides are otherwise well covered - frontend/src/lib/types.test.ts exercises parseJarvisEvent across all four states and malformed payloads, and frontend/src/hooks/useJarvisSession.test.tsx covers the pinned transcript merge rule - so this ticket adds the one assertion neither suite can make alone.
- **Acceptance**: 1) A check asserts the frontend's AGENT_STATES and the backend's _WIRE_STATES contain exactly the same members, and fails if either side gains, loses, or renames a state 2) The check reads both definitions from their source of truth rather than restating the list a third time - a hardcoded copy inside the test would be a fourth place to drift 3) The check runs in an existing verify command for one of the two sides, so no new CI job is added (SPEC C7) 4) A deliberate mismatch is shown to fail the check - demonstrated in the ticket's commit message or a temporary local edit, not left as an untested assertion 5) The frontend production bundle warning (686kB > Vite's 500kB threshold at last measurement) is either resolved or recorded in docs with the reason it is acceptable for this deliverable
- **Verify**: `cd frontend && npm run -s build && npm run -s test -- --run`
- **Scope**: `frontend/**, backend/src/jarvis/pipeline/events.py, scripts/**, docs/**`
- **Depends on**: none · **parallel_safe**: true
- **Non-goals**: No redesign of the SPA and no change to the pinned wire event shapes; Does not add a CI job or matrix - SPEC C7 is a hard constraint; Does not restate the state list anywhere it is not already defined

### T-28: T-19's verify can actually fail, and no verify string can neuter itself  `[queue]`
- **Objective**: T-19's pinned verify ends "--client echo || true": shell left-associativity makes the whole command exit 0 even when its pytest leg fails, and the inner eval leg genuinely exits 1 at HEAD (the echo client mirrors each fixture, tripping exactly 10 content guards against --max-fabrications 0 -- a known, stable outcome the pinned string discards). Confirmed still true after the T-17..T-27 batch: the chore that fixed T-23's string left T-19's untouched, and T-24's enumeration documented the neutering without repairing it (a deliberate no-retroactive-edit choice recorded in its completion commit). Nothing lints verify strings, so this class -- unconditional-success tails, cwd-leaking cd across && (both observed this build) -- can return with any future plan edit.
- **Acceptance**: 1) T-19's re-pinned verify exits nonzero when its pytest leg fails (demonstrated with a temporary local red test, then reverted) and nonzero when the echo eval's outcome deviates from its pinned expectation; no || true or equivalent unconditional-success tail remains in it 2) The echo leg stays honest per SPEC R6a: it asserts the harness's known echo outcome as a SELF-TEST (the stable fabrication set), keeps the existing not-evidence-of-grounding labeling, and is never presented as grounding evidence 3) A verify-string lint runs inline in verify_gate.sh before any ticket's verify executes, and verify_gate fails CLOSED if the lint cannot run. The rules, pinned mechanically: reject unconditional-success tails (|| true, ; true, || :), and reject an unparenthesized cd whose effect crosses && into a later leg that references a repo-root-relative path (scripts/, docs/, .claude/, frontend/) -- the T-23 defect shape; a single leading cd whose later legs stay inside that directory is conformant, so the ~20 legacy 'cd backend && ...' strings pass unchanged. Hook tests drive the lint with at least one failing fixture per rule, plus all 35 current strings passing after T-19's fix 4) Only T-19's verify string is edited in docs/tickets.json -- every other ticket's verify is byte-identical -- and both places docs/TASKS.md restates T-19's string (its T-19 section and the T-24 verify-coverage enumeration) are updated to the re-pinned string in the same commit; the lint only ADDS failure modes to the gate 5) The two T-22-era sandbox assertions in .claude/hooks/tests/run_evidence_tests.sh that pin the old permissive behavior (the T-19-shaped '|| true' case and the T-23-shaped cd case, both currently asserted as allowed) are updated to expect lint rejection; if any lint code lives outside verify_gate.sh, the sandbox copy step is updated in the same change so the sandboxed gate still runs it
- **Verify**: `bash .claude/hooks/tests/run_verify_lint_tests.sh && bash .claude/hooks/tests/run_evidence_tests.sh`
- **Scope**: `docs/tickets.json, docs/TASKS.md, .claude/hooks/**, backend/evals/**, backend/tests/grounding/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: No other verify string is changed; No change to the REFUSAL literal or pinned scoring verdict names; Does not attempt to make the echo eval a grounding gate -- real-model evidence is T-33's job

### T-29: Golden-set approval binds to the bytes it approved  `[resolved]`
- **Objective**: T-25's gate proves _meta.approved_by/approved_at are present and non-blank -- not that the approval covers the current content. Demonstrated live this build: T-19 edited the set after the 2026-08-13 approval (new G-038, tightened G-004) and every gate stayed green until out-of-band monitoring surfaced it; the 2026-08-14 re-approval (362f95a) is likewise bound to nothing, so the next edit repeats the cycle. Bind approval to content: _meta.approved_sha256 over the approved bytes (golden.jsonl normalized minus the _meta line); mismatch or absence fails the gate with the remedy named. Same audit, secondary hygiene: test_golden_ambiguity's number/url alias exemption accepts the number as ANY path segment of the url, so /orgs/42/repo/pull/999 would wrongly exempt an unrelated 42.
- **Acceptance**: 1) The approval gate fails with a distinct exit code and remedy text when golden.jsonl's current content hash differs from _meta.approved_sha256, and treats a missing hash on an otherwise-approved set the same way -- unverifiable is never current 2) A helper prints the canonical hash for a human to paste; no code path ever writes approved_sha256 into golden.jsonl (T-25's no-signing non-goal carried forward). The executing agent performs the one-time mechanical edit inserting the minted hash for the currently approved 2026-08-14 content -- required for the gate and the unmodified T-25 tests to pass against the real file -- and Hank's confirmation of that exact value is requested in the completion report 3) Existing T-25 approval-gate tests pass unmodified; new tests cover match, mismatch, and missing-hash via tmp_path sets and never edit the real golden.jsonl beyond the single _meta hash insertion above (the autouse byte-compare fixture pattern already in test_approval_gate.py) 4) The number/url exemption merges a number/url pair only when the number is the url's identifying segment, with a test that fails against the old any-segment rule
- **Verify**: `(cd backend && uv run pytest tests/grounding -q && uv run ruff check src)`
- **Scope**: `backend/evals/**, backend/tests/grounding/**`
- **Depends on**: none · **parallel_safe**: false
- **Non-goals**: No golden case content changes; No claim of authenticity beyond content binding -- a human still owns 'approved'; No change to pinned scoring verdict names

### T-30: One filler-asset source of truth  `[resolved]`
- **Objective**: T-26 fixed packaging by copying the six clips and manifest into backend/src/jarvis/cadence/assets/fillers/, leaving the originals at backend/assets/fillers/ -- and scripts/build_fillers.py still writes ONLY the old location (_DEFAULT_OUT_DIR = <repo>/backend/assets/fillers), so the first voice rebuild strands the packaged copy stale. T-26's divergence test turns that drift into a red suite, but nothing repairs it and the duplication is permanent load. Also stale: build_fillers.py's docstring cites the manifest schema as pinned in .claude/scratch/t6-contract.md, a path not in the tree.
- **Acceptance**: 1) build_fillers.py's default output is the packaged location production actually loads (backend/src/jarvis/cadence/assets/fillers/, via _resolve_assets_dir()); backend/assets/fillers/ is deleted or reduced to an explicitly gitignored scratch target, and nothing in src/ or tests/ references it -- the pipeline-side references (cadence_wiring.py and test_cadence_wiring.py) are in scope for exactly this; if the gitignored-scratch option is taken, the ignore entry lives in a nested backend/assets/.gitignore, since the root .gitignore is out of scope 2) The T-26 divergence test is retired or repointed so the suite encodes one-canonical-copy rather than two-copies-agree; the packaged copy's integrity checks (manifest matches files, bytes load into FillerBank) are not weakened 3) The manifest schema pointer names an in-repo location, not .claude/scratch/ 4) No audio bytes change: the six packaged clips and the manifest are byte-identical before and after this ticket
- **Verify**: `(cd backend && uv run pytest tests/cadence tests/pipeline -q && uv run ruff check src)`
- **Scope**: `scripts/**, backend/src/jarvis/cadence/**, backend/tests/cadence/**, backend/src/jarvis/pipeline/**, backend/tests/pipeline/**, backend/assets/**, docs/**`
- **Depends on**: T-29 · **parallel_safe**: false
- **Non-goals**: No clip regeneration -- Cartesia is returning HTTP 402 (billing), and rebuilding voices is not this ticket; No change to the FillerBank loading contract or the pinned filler phrases

### T-31: The memory-DB hard-fail flag has a real setter  `[resolved]`
- **Objective**: JARVIS_MEMORY_TESTS_REQUIRE_DB=1 turns an unreachable pgvector into a hard failure (backend/tests/memory/conftest.py:95) -- but nothing anywhere sets it: no workflow, hook, or script exports it, and the conftest text implying a deployed/CI gate context sets it is currently false. Confirmed empirically: the memory suite against an unreachable database exits 0 (15 passed, 9 skipped) with a red banner no machine reads. T-23 made the skip loud at human level; at machine level the pass is still silent, which is the exact defect class T-23 existed to end.
- **Acceptance**: 1) Exactly one owning context is wired: verify_submission.sh GAINS a memory check (it has none today) that runs the suite with JARVIS_MEMORY_TESTS_REQUIRE_DB=1, so the submission gate never tolerates an unverified memory path; local ticket verifies stay tolerant so an offline machine can still close unrelated tickets 2) A script test proves the wired context fails on an unreachable database and passes with a reachable one -- the reachable case may be simulated by a stubbed suite inside the sandbox project dir, per the harness's existing stub convention, since the subject is verify_submission.sh's flag wiring and exit-code plumbing, not live DB connectivity -- driving the real verify_submission.sh code path (sandbox project-dir argument) 3) The conftest comment names the actual setter, and docs record which gate requires the database and what credentials it needs 4) Existing memory test modules remain byte-for-byte unmodified; the only in-package edit is the conftest.py comment correction required by the previous item
- **Verify**: `(cd backend && uv run pytest tests/memory -q) && bash scripts/tests/run_require_db_wiring_tests.sh`
- **Scope**: `scripts/**, backend/tests/memory/**, docs/**`
- **Depends on**: T-30 · **parallel_safe**: false
- **Non-goals**: Does not make local ticket verifies require a database; No change to the memory implementation or its test modules

### T-32: The worker-liveness probe fails closed on shape drift  `[resolved]`
- **Objective**: T-23's rebuilt worker check parses flyctl machine list -j with an audit-confirmed fail-open branch: an exit event whose timestamp is missing or unparseable is silently skipped, so exactly the signal the check exists for -- a recent crash -- can vanish behind a green verdict. Adjacent wrong shapes fail only by accident: events as a dict or a non-dict event entry crashes the embedded parser, and the shell catch-all converts the crash into an unhelpful 'no parseable verdict' failure rather than a named one. T-23's completion commit claimed three wrong-JSON-shape probes 'fail loudly', but zero are committed (they were ad-hoc dev-time checks), so this regression class is unguarded. The field shape has never been validated against a live authenticated flyctl -- auth remains a human-blocked prerequisite.
- **Acceptance**: 1) An exit event with a missing or unparseable timestamp makes the probe report UNKNOWN/fail -- proven to invert against the old silent-skip behavior; events as a non-list and a non-dict entry in events[] produce an explicit UNKNOWN/fail with a named reason instead of a crash-derived verdict; a machine entry missing state continues to fail as it does today; legitimately absent non-exit events remain fine 2) Wrong-shape probe cases are committed into scripts/tests/run_smoke_exit_code_tests.sh (at minimum: events as a dict, exit event with a garbage timestamp, machine entry missing state); the garbage-timestamp case is the one proven to invert against the old behavior, the others assert the named-reason failure 3) The stable-window and Fly-checks requirements from T-23 are unchanged and no existing harness case is weakened 4) docs/ops-smoke-test.md gains the one-step instruction for capturing a sanitized live fixture on first authenticated run, and the harness accepts such a fixture as an additional case when present
- **Verify**: `bash scripts/tests/run_smoke_exit_code_tests.sh`
- **Scope**: `scripts/**, docs/**`
- **Depends on**: T-31 · **parallel_safe**: false
- **Non-goals**: No live flyctl call in verify -- authentication is a human-blocked prerequisite; No change to T-23's credential-failure semantics (unauthenticated still hard-fails)

### T-33: Real eval runs leave committed artifacts  `[resolved]`
- **Objective**: T-18 and T-19 both cite real-Anthropic eval results (PASS: 37 and 38 cases respectively, 0 fabrications, ...) that exist nowhere but commit-message prose -- the audit could not verify either from the repo, and T-18's acceptance-3 half about docs recording the re-run is still unmet at HEAD because its scope had no docs path. The plumbing already exists unused: evals.run supports --json (run.py) and _write_json_report writes a full structured report. Make committed artifacts the convention and retire commit-prose as eval evidence.
- **Acceptance**: 1) A fresh real-model eval (default Anthropic client, models from $CONVO_MODEL/$TOOL_MODEL routing) over the re-approved golden set runs with --json and the report is committed at docs/evals/latest.json; the summary shows 0 fabrications, 0 wrong refusals, 0 missing includes, 0 errors, or the failure is surfaced to the human rather than retried into silence 2) docs/evals/README.md names the convention -- every real run writes a JSON report, committed with the change it evidences -- and points at the latest report; T-18's outstanding docs-half is explicitly satisfied by reference to it 3) scripts/check_eval_report.py validates the committed report's schema and zero-failure summary so a hand-edited or truncated report cannot pass; it is stdlib-only, since the verify runs it under bare python3 outside the uv environment 4) golden.jsonl is untouched and no model id is hardcoded anywhere
- **Verify**: `(cd backend && uv run pytest tests/grounding -q) && python3 scripts/check_eval_report.py docs/evals/latest.json`
- **Scope**: `backend/evals/**, docs/**, scripts/**`
- **Depends on**: T-32 · **parallel_safe**: false
- **Non-goals**: No API keys or secrets committed; No golden-set edits and no eval logic changes beyond report plumbing

### T-34: A missing receipt is a loud state, and every closed ticket has one  `[queue]`
- **Objective**: .claude/evidence/ holds fingerprint receipts for every ticket that has closed except one -- T-22, the ticket that invented fingerprinting, which never minted its own -- plus T-13, which has no receipt because it legitimately cannot close yet (human-blocked, per its completion commit). verify_submission.sh's freshness gate (seeded by T-22) judges receipts that exist; a ticket with NO receipt must be its own explicit failure class, distinguished from STALE/UNVERIFIABLE, and mapped to the documented human-action list when that is the true cause -- otherwise absence reads as either an opaque failure or nothing at all.
- **Acceptance**: 1) verify_submission.sh enumerates every ticket id in docs/tickets.json and reports each with no receipt as MISSING -- its own labeled failure, counted separately from STALE/UNVERIFIABLE in the run summary 2) T-22's receipt is minted by re-running its verify through the real verify_gate path (not hand-written), after T-34's last edit to any file in T-22's scope, and the freshness gate then reports it CURRENT as of T-34's completion -- acknowledging the recorded scope-granularity residual re-stales it on future scripts/** changes 3) T-13's MISSING maps in output to the human-action list in docs/SUBMISSION.md rather than presenting as unexplained -- without being exempted: it still fails the gate until the ticket truly closes 4) The evidence hook test suite gains MISSING-classification cases and all existing evidence tests pass unmodified beyond what T-28 already changed
- **Verify**: `test -f .claude/evidence/T-22.json && bash scripts/tests/run_evidence_tests.sh && bash scripts/tests/run_require_db_wiring_tests.sh`
- **Scope**: `scripts/**, docs/**`
- **Depends on**: T-33 · **parallel_safe**: false
- **Non-goals**: Does not mint T-13's receipt or complete T-13 -- that closure is human-blocked; Does not relax the freshness gate for receipts that do exist
