"""Bash-command guard for the agent-PR job (R10 / T-11 acceptance #4).

**Positive-permission, not a deny-list.** An earlier version of this module
matched raw text against a small set of "dangerous" regexes (``gh pr
merge``, ``--force``, ...). An independent verifier defeated it six
different ways -- quoting (``gh "pr" merge``), a raw ``curl`` call at the
merge REST endpoint (no git/gh pattern to match at all), ``+refspec``
force-push syntax, a backslash-newline shell continuation joining two
lines into one command, ``git config alias.*``/``gh alias`` indirection
across separate Bash calls, and writing a script file (via the ``Write``
tool) and running it with ``bash script.sh`` -- none of which any
finite deny-list can be complete against. Enumerating dangerous strings is
an unwinnable arms race.

This module instead answers "is ``command`` one of the small set of
operations this job is allowed to run at all" and refuses everything else
by default -- including anything it cannot confidently reduce to a single,
plain ``git``/``gh`` invocation (chained commands, redirection, command
substitution, embedded newlines). A novel evasion therefore fails closed
instead of needing its own detector:

- **Binary allow-list of exactly ``git``/``gh``** closes the script-
  indirection hole structurally: the ``Write`` tool can create any file it
  wants, but no allowed Bash invocation can ever *execute* one (no ``bash``,
  ``sh``, ``python``, ``./script.sh``, ...) -- so gating ``Write`` itself, or
  scanning what it writes, is unnecessary. It also closes the raw-``curl``
  hole: curl (or any other HTTP client) is simply not on the allow-list, so
  no REST/GraphQL escape hatch exists regardless of the URL it targets.
- **Exact per-subcommand argument shapes** (rather than "does this string
  contain a bad word") close the quoting bypass -- ``gh "pr" merge``
  shell-tokenizes identically to ``gh pr merge`` (see ``_tokenize``), so
  quoting one token buys nothing.
- **``git config`` and ``gh alias`` are refused outright**, closing the
  alias-indirection vector explicitly. Even without that explicit rule, an
  aliased subcommand like ``git mg`` would still fall through every
  recognized shape to the default-deny branch, because this is an
  allow-list: an unrecognized subcommand is refused by construction, not
  because "mg" was specifically flagged as suspicious.
- **Any embedded newline, or command substitution, refuses the whole
  command outright** (``_tokenize`` returns ``None``) -- this is what
  closes the backslash-newline shell-continuation bypass; there is no
  attempt to "join and re-check" it, because that reasoning is exactly what
  the deny-list arms race required and lost.
- **Any control/redirection operator (``&&``, ``||``, ``;``, ``|``, ``&``,
  ``(``, ``)``, ``<``, ``>``) also refuses the whole command outright** --
  a Bash call may run exactly one recognized ``git``/``gh`` invocation, full
  stop. This is *more* restrictive than "validate every piece of a chain",
  deliberately: chaining a safe op with an unsafe one is exactly the kind
  of case a per-piece validator could get wrong, and the agent can always
  issue two separate Bash tool calls instead.

See ``claude_runner.py`` for how this is wired into a ``PreToolUse`` hook
(chosen over ``can_use_tool`` because a hook fires "regardless of
permission rules", per the Claude Agent SDK's own docs -- ``can_use_tool``
is skipped for any tool call an ``allowed_tools``/``permission_mode`` rule
already resolved, which Bash always is here).
"""

from __future__ import annotations

import re
import shlex

# Refuses the whole command outright -- these make the raw text unsafe to
# reason about via simple tokenization at all:
#   \$\(   command substitution, `$(...)`
#   `      command substitution, backticks
#   \r|\n  any embedded newline, INCLUDING the backslash-newline shell
#          line-continuation form: POSIX shells join `cmd \` + newline +
#          `rest` into one line before executing it, so scanning the raw
#          text for a literal newline character is sufficient to catch it
#          without separately modeling shell continuation syntax.
_UNSAFE_RAW_RE = re.compile(r"\$\(|`|\r|\n")

# shlex's `punctuation_chars=True` mode tokenizes these as their own tokens
# when they appear outside quotes (and only outside quotes -- `"a && b"` is
# one token, `a && b` is three) -- exactly the shell-aware split we need to
# both catch chaining and NOT false-positive on a commit message containing
# a literal "&" or ";" inside quotes. `;` isn't in shlex's punctuation set
# but is already excluded from its default wordchars, so it splits too; `&&`
# and `||` come through as their own two-character tokens, not `&`+`&`.
_CONTROL_TOKENS = frozenset({"&&", "||", ";", "|", "&", "(", ")", "<", ">"})

_ALLOWED_BINARIES = frozenset({"git", "gh"})

# Case-insensitive: refusing a push to a branch merely differently-cased
# from "main"/"master" costs nothing (false positives fail closed and are
# harmless -- the agent tries a different branch name) and closes a trivial
# evasion attempt, even though git refs are technically case-sensitive.
_PROTECTED_BRANCHES = frozenset({"main", "master", "head"})

_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes"})

_ALLOWED_GH_PR_ACTIONS = frozenset({"create", "view", "list", "diff", "status"})
_ALLOWED_GH_READ_ONLY_SUBCOMMANDS = frozenset({"issue", "repo"})
_ALLOWED_GH_READ_ONLY_ACTIONS = frozenset({"view", "list", "status"})

# Allowed unconditionally, with no argument-shape check beyond the global
# force-flag/`git config` rejection above: `add` mutates only the index
# (never history, never a remote), and the rest are genuinely read-only.
# None of them can merge or force-push.
_UNCONDITIONALLY_ALLOWED_GIT_SUBCOMMANDS = frozenset(
    {"add", "status", "diff", "log", "show", "rev-parse"}
)


def _tokenize(command: str) -> list[str] | None:
    """Reduce ``command`` to the argv of exactly ONE simple invocation, or
    ``None`` (fail closed) if it is anything this allow-list cannot safely
    reason about that way: command substitution, an embedded newline
    (including a backslash-newline continuation), unparseable/unterminated
    quoting, or any control/redirection operator chaining multiple
    commands together.

    Shell-aware (respects quoting) via ``shlex`` in POSIX mode with
    ``punctuation_chars`` enabled, so ``gh "pr" merge`` tokenizes
    identically to ``gh pr merge`` -- quoting a token buys nothing.
    Comment stripping is explicitly disabled: shlex's default treats a
    bare ``#`` as "rest of line is a comment" and silently drops it, which
    would make this function *approve* a truncated view of a command while
    a real shell might interpret the untruncated text differently (a
    parser-differential bug class). With comments disabled, anything after
    a stray ``#`` is visible as ordinary tokens instead -- typically
    tripping the control-operator check below and failing closed, which is
    the safe direction to be wrong in.
    """
    if _UNSAFE_RAW_RE.search(command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None  # unterminated quote, trailing backslash, etc.
    if not tokens or any(tok in _CONTROL_TOKENS for tok in tokens):
        return None
    return tokens


def _has_force_flag(args: list[str]) -> bool:
    return any(a in _FORCE_FLAGS or a.startswith("+") for a in args)


def _check_git_push(rest: list[str]) -> tuple[bool, str]:
    positional = [a for a in rest if a not in ("-u", "--set-upstream")]
    if len(positional) != 2:
        return False, "only `git push [-u] <remote> <branch>` is allowed"
    remote, branch = positional
    if remote != "origin":
        return False, "the only allowed push remote is 'origin'"
    if ":" in branch:
        return False, "explicit local:remote refspecs are not allowed -- push a plain branch name"
    if branch.lstrip("+").lower() in _PROTECTED_BRANCHES:
        return False, "pushing directly to main/master is never allowed (R10)"
    return True, ""


def _check_git(args: list[str]) -> tuple[bool, str]:
    if not args:
        return False, "bare `git` with no subcommand is not allowed"
    subcommand, rest = args[0], args[1:]

    if subcommand == "merge":
        return False, "`git merge` is never allowed (R10)"
    if subcommand == "config":
        return False, "`git config` is never allowed (blocks alias-based indirection)"
    if _has_force_flag(rest):
        return False, "force flags / '+refspec' force-push syntax are never allowed (R10)"

    if subcommand == "checkout":
        # Only the "create a new branch" shape; switching to an existing
        # branch or discarding changes (`git checkout main`, `git checkout
        # -- file`) is outside what this job needs.
        if rest[:1] == ["-b"] and len(rest) >= 2:
            return True, ""
        return False, "only `git checkout -b <branch> [<start-point>]` is allowed"

    if subcommand in _UNCONDITIONALLY_ALLOWED_GIT_SUBCOMMANDS:
        return True, ""

    if subcommand == "branch":
        # Bare/read-only listing only -- no -d/-D/-m/-M (delete/rename).
        if not any(a.startswith("-") for a in rest):
            return True, ""
        return False, "only a bare, read-only `git branch` is allowed"

    if subcommand == "commit":
        if "-m" in rest or "--message" in rest or "-am" in rest:
            return True, ""
        return False, "only `git commit -m <message>` is allowed"

    if subcommand == "push":
        return _check_git_push(rest)

    return False, f"`git {subcommand}` is not on the allow-list"


def _check_gh(args: list[str]) -> tuple[bool, str]:
    if not args:
        return False, "bare `gh` with no subcommand is not allowed"
    subcommand, rest = args[0], args[1:]

    if subcommand == "alias":
        return False, "`gh alias` is never allowed (blocks alias-based indirection)"
    if subcommand == "api":
        return False, "`gh api` is never allowed -- it can call the merge endpoint directly (R10)"

    if subcommand == "pr":
        if rest[:1] and rest[0] in _ALLOWED_GH_PR_ACTIONS:
            return True, ""
        return False, "only `gh pr create`/`view`/`list`/`diff`/`status` are allowed"

    if subcommand in _ALLOWED_GH_READ_ONLY_SUBCOMMANDS:
        if rest[:1] and rest[0] in _ALLOWED_GH_READ_ONLY_ACTIONS:
            return True, ""
        return False, f"only read-only `gh {subcommand}` actions are allowed"

    return False, f"`gh {subcommand}` is not on the allow-list"


def is_allowed_bash_command(command: str) -> tuple[bool, str]:
    """Positive-permission check for one Bash tool_use command (R10 / T-11
    acceptance #4).

    Returns ``(True, "")`` iff ``command`` is exactly one of the small set
    of ``git``/``gh`` operations this job needs (clone happens outside the
    agent's Bash access entirely, via ``workdir.py``): create a branch, add,
    commit, push a non-force ref to a non-protected branch, `gh pr create`,
    plus a handful of read-only introspection commands. Everything else --
    including anything that cannot be reduced to a single simple invocation
    at all -- returns ``(False, <reason>)``. Deny by default.
    """
    tokens = _tokenize(command)
    if tokens is None:
        return False, "not a single, plain git/gh invocation this job is allowed to run"

    binary, args = tokens[0], tokens[1:]
    if binary not in _ALLOWED_BINARIES:
        return False, f"binary {binary!r} is not on the allow-list (only git and gh are permitted)"
    if binary == "git":
        return _check_git(args)
    return _check_gh(args)
