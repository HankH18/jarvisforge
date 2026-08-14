# Agent-PR demo (T-11 acceptance #3)

**Status: DONE — live run recorded 2026-08-14.** `run_fix_job` was run for
real against a coordinator-provided private testbed repo, using the real
Claude Agent SDK (headless Claude Code CLI) and the real `GITHUB_TOKEN` from
`.env`. It opened a pull request and did not merge or force-push anything.
Full details, the Bash calls the allow-list hook refused during the run,
and independent `gh`-based verification are below.

## The run

| | |
|---|---|
| Repo | `https://github.com/HankH18/jarvis-agent-pr-testbed` (private) |
| Issue | [#1 — "greet() ignores the name argument"](https://github.com/HankH18/jarvis-agent-pr-testbed/issues/1) |
| `AGENT_PR_ALLOWED_REPOS` used | `https://github.com/HankH18/jarvis-agent-pr-testbed` (exactly this one repo, nothing else) |
| Model | `claude-sonnet-5` (from `$TOOL_MODEL`, per SPEC C3 — never hardcoded) |
| Feature branch | `fix-greet-name-arg` |
| **PR opened** | **[#2 — "Fix greet() to use the name argument"](https://github.com/HankH18/jarvis-agent-pr-testbed/pull/2)** |
| Job's final state | `pr_opened` (no error) |

Driven via `execute_fix_job(...)` directly (real `ClaudeCodeAgentRunner`,
real `prepare_workdir`/`cleanup_workdir`, a recording `Announcer`) from a
throwaway script outside the repo, so it could be awaited to completion and
its result captured — `run_fix_job` itself (the pinned fire-and-forget
entry point) is exercised identically by the unit tests in
`test_job_orchestration.py`, including its own repo-allow-list and
GITHUB_TOKEN checks.

**What the agent changed** (`gh pr diff 2`):

```diff
diff --git a/greet.py b/greet.py
--- a/greet.py
+++ b/greet.py
@@ -1,6 +1,5 @@
 def greet(name):
-    # BUG: ignores the name argument entirely
-    return "Hello, world!"
+    return f"Hello, {name}!"

diff --git a/test_greet.py b/test_greet.py
new file mode 100644
--- /dev/null
+++ b/test_greet.py
@@ -0,0 +1,5 @@
+from greet import greet
+
+
+def test_greet_uses_name():
+    assert greet("Jarvis") == "Hello, Jarvis!"
```

One commit on the feature branch: `b5983c661980` — "Fix greet() to use the
name argument, add test".

**Announcements** (acceptance #2): all three state transitions were
announced to both the (stubbed, no live LiveKit room) data-channel
publisher and the TTS `speak` callable, timestamped:

```
[1786738943.980] Starting on issue #1. This will take a few minutes.
[1786738953.723] Still working on issue #1.
[1786739045.419] Opened a pull request for issue #1: https://github.com/HankH18/jarvis-agent-pr-testbed/pull/2
```

**Not audible**: Cartesia TTS is down (HTTP 402, "Model credits limit
reached", independently confirmed earlier in this build) and this run used
a recording stub for `Announcer`'s `speak`, not a real TTS provider — so
the audible half of "audible progress" could not be demonstrated live. The
mocked-agent unit tests (`test_announcer.py`, `test_job_orchestration.py`)
already prove announcements reach both the data-channel and TTS boundary
correctly, which is what acceptance #2 asks; this run additionally proves
the *real* agent (not a scripted double) actually reaches all three
transitions end to end.

## Bash calls the allow-list hook refused during the run

Three, all logged by `claude_runner._enforce_bash_allow_list` (added for
exactly this purpose — see the run log). The job succeeded despite all
three: the agent adjusted its approach after each refusal, as the system
prompt instructs, rather than getting stuck.

1. **`ls -la`** — denied: `ls` is not on the allow-list (only `git`/`gh`
   binaries are). **Finding**: this is a completely benign, read-only
   command a coding agent naturally reaches for to orient itself, and it's
   not one of the operations R10 cares about. It didn't block progress here
   (the agent has `Glob`/`Read`/`Grep` as SDK tools, which don't go through
   Bash at all, and used those instead), but it's real evidence the
   allow-list is narrower than "everything a coding agent would want" —
   deliberately, per the coordinator's brief ("a small, explicit set of
   operations the job actually needs"), not by oversight.
2. **`git commit -m "$(cat <<'EOF' ... multi-paragraph message ... EOF)"`**
   — denied: contains both command substitution (`$(`) and embedded
   newlines, so `_tokenize` refuses it outright before it's even checked
   against a subcommand shape. **Finding**: this is the standard git
   convention for a summary line + blank line + body commit message, and
   the guard forces single-line commit messages instead. The agent adapted
   and committed successfully with a plain `-m "<one line>"` on the next
   attempt.
3. **`gh pr create --repo ... --title "..." --body "Fixes #1.\n\n<multi-paragraph body>"`**
   — denied for the same reason (embedded newlines in the `--body` value).
   **Finding**: same tradeoff as #2, applied to the PR body. Notably, `gh
   pr create --body-file <path>` would have avoided this entirely (write
   the body to a file via the `Write` tool, pass the *filename* as a single
   plain argument — no newline ever appears in the Bash command itself) and
   is already allowed by the current rules (`gh pr create`'s arguments
   aren't shape-restricted beyond the subcommand check) — the agent didn't
   happen to try that path, it just retried with a single-line body and
   succeeded.

None of these three are reported as bugs to fix — per the coordinator's
instruction, the allow-list was not widened to accommodate them. They're
recorded here as the disclosed usability cost of the newline/
command-substitution-refuses-everything rule from `guard.py`'s "Known
residual" section, now demonstrated against a real agent instead of only
reasoned about.

## Independent verification (`gh`, read-only, after the run)

```
$ gh pr view 2 --repo HankH18/jarvis-agent-pr-testbed --json number,state,closed,mergedAt,mergeCommit,headRefName,baseRefName
{"baseRefName":"main","closed":false,"headRefName":"fix-greet-name-arg",
 "mergeCommit":null,"mergedAt":null,"number":2,"state":"OPEN"}
```
→ **PR #2 is OPEN, `closed: false`, `mergedAt: null`, `mergeCommit: null` — not merged.**

```
$ gh api "repos/HankH18/jarvis-agent-pr-testbed/commits?sha=main" \
    --jq '.[] | {sha: .sha[0:12], message: .commit.message}'
{"sha":"aca88155f407","message":"Initial testbed: greet() ignores its name argument"}
```
→ **`main` has exactly one commit — the coordinator's original setup commit. Nothing was pushed to `main`, force or otherwise; its history is unchanged.**

```
$ gh api "repos/HankH18/jarvis-agent-pr-testbed/compare/main...fix-greet-name-arg" \
    --jq '{ahead_by, behind_by, status}'
{"ahead_by":1,"behind_by":0,"status":"ahead"}
```
→ **The feature branch is exactly 1 commit ahead of `main` and 0 behind — a clean fast-forward add, confirming the fix landed on `fix-greet-name-arg` and `main` was neither rewound nor rewritten.**

```
$ gh api repos/HankH18/jarvis-agent-pr-testbed/branches --jq '.[].name'
fix-greet-name-arg
main
```
→ **Only the two expected branches exist — no stray branches from a failed/retried attempt.**

## Bash guard: positive-permission allow-list (R10)

An earlier version of the Bash guard (`guard.py`) matched raw command text
against a small set of "dangerous" patterns (`gh pr merge`, `--force`, ...).
An independent verifier defeated it six different ways — quoting a single
token (`gh "pr" merge`), a raw `curl` call straight at the GitHub REST merge
endpoint (no `gh`/`git` pattern to match at all), `git push origin +main`
(the `+refspec` force-push shorthand), a backslash-newline shell
continuation joining two lines into one command, `git config alias.*` /
`gh alias` indirection across separate Bash calls, and writing a script via
the `Write` tool and running it with `bash script.sh`. Enumerating
dangerous strings is an unwinnable arms race.

`guard.py` was rewritten as a **positive-permission allow-list**: deny by
default, and allow only the small set of plain `git`/`gh` invocations this
job actually needs (create a branch, add, commit, push a non-force ref to a
non-`main`/`master` branch, `gh pr create`, plus a few read-only
introspection commands). A Bash call is refused outright — no per-command
"is this piece dangerous" check — if it isn't reducible to exactly one
plain invocation: any shell chaining (`&&`, `;`, `|`, `&`), redirection
(`<`/`>`), command substitution (`` $(...) ``/backticks), or embedded
newline (including the backslash-newline continuation form) fails closed.

How each of the six verifier-found bypasses is closed now (all six have a
dedicated test in `backend/tests/agent_pr/test_guard.py`):

1. **Quoting** (`gh "pr" merge`) — commands are tokenized shell-aware via
   `shlex` (POSIX mode, `punctuation_chars` enabled), so quoting a token
   changes nothing about the parsed argv the allow-list actually checks.
2. **Raw `curl`/REST** — the allow-list is a fixed *binary* allow-list of
   exactly `git` and `gh`. `curl` (or any other HTTP client) is refused
   because it isn't `git`/`gh`, not because of a merge-endpoint pattern.
3. **`+refspec` force-push** (`git push origin +main`) — the push validator
   refuses any push argument starting with `+`, in addition to
   `--force`/`--force-with-lease`/`-f`.
4. **Backslash-newline continuation** — any embedded newline in the raw
   command text refuses the whole command outright; there is no attempt to
   join-and-recheck it.
5. **Alias indirection** (`git config alias.mg merge`, `gh alias set ...`)
   — `git config` and `gh alias` are refused outright. Even without that
   explicit rule, invoking the alias afterward (`git mg main`) would still
   fall through every recognized shape to default-deny, because an
   allow-list refuses any subcommand it doesn't recognize by construction.
6. **Script indirection** (`Write` a script, then `bash script.sh`) —
   closed structurally by the binary allow-list itself: `Write` stays
   granted (the fix has to be authored somehow), but no allowed Bash
   invocation can ever *execute* a file it wrote, since `bash`/`sh`/
   `python`/`./script.sh`/etc. are simply not `git` or `gh`. This was
   chosen over the alternatives the verifier offered (gate the tools that
   create executables, or refuse Bash invocations of files the job wrote)
   because it requires no file-content inspection and no new hook on
   `Write`, and over dropping `Write` entirely because the job has to be
   able to edit the repo's source to produce a fix at all.

**Known residual (disclosed, not closed):** an approved argument containing
literal shell parameter expansion (`$SOME_VAR`, as opposed to `` $(...) ``
command substitution, which *is* blocked) is not specifically rejected.
Exploiting it would require the agent to first get an attacker-influenced
value into that variable — which itself requires a command this guard
already refuses (`export`, `env`, `set`, or any binary other than
`git`/`gh`) — so this is a theoretical gap under the current design, not a
demonstrated one, and no test claims otherwise.

Everything else the verifier listed as "correctly caught today" under the
old deny-list (`gh  pr  merge` with extra spaces, `GH_TOKEN=x gh pr merge`,
`--force-with-lease`, `-f`, `bash -c "gh pr merge"`, `eval "..."`) is
covered by dedicated regression tests in `test_guard.py` too, so the
rewrite is checked not to have lost ground on those.

## Repo allow-list (verifier finding G)

`run_fix_job`/`prepare_workdir` previously accepted **any** `https://` URL
— nothing tied the target repo to one the user actually controls (SPEC
non-goal: "no arbitrary-repo writes; any write action on repos the user
doesn't control"). That this was safe in practice was because the package
was never wired into a path that could pass an attacker- or user-supplied
URL through — luck, not a safeguard.

`repo_allowlist.py` now makes this an enforced precondition: `repo_url`
must appear (case-insensitively, trailing slash ignored) in the
comma-separated env var `AGENT_PR_ALLOWED_REPOS`, checked both
synchronously in `run_fix_job` (before anything is even scheduled) and
again inside `execute_fix_job` (so calling it directly, as the unit tests
do, is protected too). An unset or empty allow-list permits nothing — deny
by default. See `backend/tests/agent_pr/test_repo_allowlist.py` and the
dedicated allow-list-denial tests in `test_job_orchestration.py`.

**Confirmed working in the live run above**: `AGENT_PR_ALLOWED_REPOS` was
set to exactly `https://github.com/HankH18/jarvis-agent-pr-testbed` (one
value, nothing else) before the run, and the job never touched any other
repo. Outside of this recorded run, the env var is unset in this codebase's
deployment config, so `run_fix_job` currently refuses every repo by
default until it's configured for a real deployment.

## Token-scope finding (T-11 acceptance #4 — UNMET, code cannot fix this)

`GITHUB_TOKEN` is a classic PAT with OAuth scopes `admin:public_key, gist,
read:org, repo` (confirmed via `curl -sI -H "Authorization: token
$GITHUB_TOKEN" https://api.github.com` → `x-oauth-scopes` header; an
independent verifier confirmed this too).

**The `repo` scope genuinely grants merge and force-push capability** —
via both the git protocol and the REST/GraphQL APIs — across every
repository the token's owner (`HankH18`) has push access to, not just a
single designated test repo. Acceptance #4 reads "no merge or force-push
capability **in the token scope**" — that is a property of the credential
itself, and this token does not have it. **This is unmet, and no code
change can fix it**: the Bash allow-list above is a real, tested control,
but it is a *different, weaker* one than acceptance #4 asks for — it
constrains what this job's own agent will attempt, not what the token
itself is capable of if used any other way (a bug elsewhere in this
codebase, a different tool, a compromised dependency, ...).

**Only Hank can close this**, by minting a credential that is actually
incapable of merging or force-pushing, scoped to only the testbed repo.
Exact spec for a **fine-grained personal access token**
(github.com → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token):

| Field | Value |
|---|---|
| Resource owner | `HankH18` |
| Repository access | **Only select repositories** → the one testbed repo (once it exists) |
| Contents | **Read and write** — needed to clone/push a branch |
| Pull requests | **Read and write** — needed to open the PR (`gh pr create`) |
| Metadata | Read-only (mandatory, auto-selected) |
| **Administration** | **Not granted** — this is the permission that controls branch-protection bypass; a token without it cannot force-push over `main`'s protection or force-merge |
| Everything else (Actions, Issues, Workflows, Secrets, Webhooks, ...) | **Not granted** |

With `Administration` withheld and `Contents`/`Pull requests` capped at
exactly what `gh pr create` and a normal `git push` need, this token is
**structurally unable** to merge a PR via the API (merging is gated
separately from PR creation) or to force-push past branch protection if the
testbed repo has any configured — making acceptance #4 a property of the
credential itself, not just of this codebase's guard. Replace the classic
`GITHUB_TOKEN` with this fine-grained token (scoped to the testbed repo
only) before the live run, and re-check the `x-oauth-scopes`-equivalent for
fine-grained tokens (`GET /repos/{owner}/{repo}/collaborators/{user}/permission`
or simply attempting a merge against a disposable test PR and confirming it
is rejected) to confirm before marking acceptance #4 met.
