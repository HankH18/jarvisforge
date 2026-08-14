"""guard.is_allowed_bash_command: R10's positive-permission Bash allow-list
(T-11 acceptance #4).

This module was rewritten after an independent verifier defeated the
original deny-list six different ways (see git history / the module
docstring for the full reasoning). Every one of those six bypasses gets its
own test below, plus the six cases the deny-list already caught (to prove
the rewrite didn't regress them), plus general allow-list coverage.
"""

from __future__ import annotations

import pytest

from jarvis.agents_pr.guard import is_allowed_bash_command


def _denied(command: str) -> None:
    allowed, reason = is_allowed_bash_command(command)
    assert allowed is False, f"expected {command!r} to be denied, got allowed ({reason!r})"
    assert reason  # every denial carries an explanation


def _allowed(command: str) -> None:
    allowed, reason = is_allowed_bash_command(command)
    assert allowed is True, f"expected {command!r} to be allowed, got denied: {reason!r}"


# ---- the six bypasses the verifier found in the original deny-list -------


def test_bypass_1_quoting_a_single_token_does_not_evade_the_allow_list():
    _denied('gh "pr" merge')
    _denied("gh 'pr' 'merge'")


def test_bypass_2_curl_at_the_merge_rest_endpoint_is_refused():
    _denied(
        'curl -X PUT -H "Authorization: token $X" '
        "https://api.github.com/repos/o/r/pulls/1/merge"
    )
    # any HTTP client, not just curl -- refused because it's not `git`/`gh`,
    # not because of a merge-endpoint pattern match
    _denied("wget https://api.github.com/repos/o/r/pulls/1/merge")


def test_bypass_3_plus_refspec_force_push_syntax_is_refused():
    _denied("git push origin +main")
    _denied("git push origin +fix-branch:main")


def test_bypass_4_backslash_newline_continuation_is_refused():
    _denied("git push \\\norigin main --force")
    # a bare embedded newline (no backslash) is refused the same way
    _denied("git push\norigin main --force")


def test_bypass_5_alias_indirection_is_refused_on_every_call():
    # setting the alias is refused outright...
    _denied("git config alias.mg merge")
    _denied("gh alias set mg 'pr merge'")
    # ...and even if it had been set some other way, invoking it is refused
    # too -- not because "mg" is specifically flagged, but because an
    # allow-list denies any subcommand it doesn't recognize by construction
    _denied("git mg main")
    _denied("gh mg 42")


def test_bypass_6_script_indirection_via_the_write_tool_is_refused():
    # Write can create any file; Bash can never execute one, because the
    # allowed-binary set is exactly {git, gh} -- no bash/sh/python/./script.
    _denied("bash script.sh")
    _denied("sh script.sh")
    _denied("bash -c script.sh")
    _denied("./script.sh")
    _denied("python3 script.py")
    _denied("source script.sh")


# ---- the six cases the original deny-list already caught (no regression) -


def test_previously_caught_double_spaced_merge():
    _denied("gh  pr  merge")


def test_previously_caught_env_prefixed_merge():
    _denied("GH_TOKEN=x gh pr merge")


def test_previously_caught_force_with_lease():
    _denied("git push --force-with-lease origin fix-branch")


def test_previously_caught_short_force_flag():
    _denied("git push -f origin fix-branch")


def test_previously_caught_bash_c_indirection():
    _denied('bash -c "gh pr merge"')


def test_previously_caught_eval_indirection():
    _denied('eval "gh pr merge"')


# ---- allow-list: operations the job legitimately needs -------------------


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -b fix/issue-42",
        "git checkout -b fix/issue-42 origin/main",
        "git add .",
        "git add -- file.py",
        'git commit -m "Fix the bug"',
        'git commit -am "Fix the bug"',
        "git push origin fix/issue-42",
        "git push -u origin fix/issue-42",
        "git status",
        "git diff",
        "git log --oneline -5",
        "git show HEAD",
        "git rev-parse HEAD",
        "git branch",
        'gh pr create --title "Fix the bug" --body "Closes #42"',
        "gh pr view 7",
        "gh pr list",
        "gh issue view 42",
        "gh repo view",
    ],
)
def test_allows_the_operations_the_job_needs(command):
    _allowed(command)


# ---- allow-list: everything else denied by default ------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git merge feature-branch",
        "git merge --no-ff feature-branch",
        "gh pr merge 42",
        "gh pr merge 42 --squash",
        "gh pr merge --auto --squash 42",
        "gh api repos/o/r/pulls/42/merge -X PUT",
        "git push origin main",  # no force, but still a direct push to main
        "git push origin master",
        "git push origin HEAD:main",  # explicit refspec to main
        "git checkout main",  # switching branches, not creating one
        "git checkout -- file.py",  # discarding local changes
        "git branch -D old-branch",
        "git reset --hard HEAD~1",
        "git rebase -i HEAD~3",
        "git clean -fd",
        "rm -rf /",
        "npm install",
        "python3 -c \"import os; os.system('gh pr merge 1')\"",
    ],
)
def test_denies_everything_not_explicitly_allowed(command):
    _denied(command)


# ---- allow-list: chaining/redirection refuse the whole command -----------


@pytest.mark.parametrize(
    "command",
    [
        "git add . && gh pr merge 1",
        "git add . && git commit -m x",  # even an all-safe chain is refused
        "git status; gh pr merge 1",
        "git status | cat",
        "gh pr create --title x --body y > out.txt",
        "git commit -m $(cat message.txt)",
        "git commit -m `cat message.txt`",
        "git status &",
    ],
)
def test_control_and_redirection_operators_refuse_the_whole_command(command):
    _denied(command)


# ---- allow-list: malformed input fails closed, not with an exception -----


def test_unterminated_quote_fails_closed_without_raising():
    allowed, reason = is_allowed_bash_command('gh pr create --title "unterminated')
    assert allowed is False
    assert reason


def test_empty_command_is_denied():
    _denied("")
    _denied("   ")
