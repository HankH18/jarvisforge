#!/usr/bin/env python3
"""Fix: exclude META_ALLOW paths from the receipt fingerprint.

Why: cmd_close computes fingerprint(scope) and THEN runs gen_tasks.py, which
rewrites docs/TASKS.md. docs/TASKS.md matches the `docs/**` glob in most ticket
scopes, so the receipt's fingerprint was stale the instant it was minted -- the
next ticket-start's `git add -A` sweeps the change in. A drift detector that
fires on the harness's own bookkeeping cannot detect real post-verification
drift. (Found by watchdog P, 2026-08-15.)

Idempotent and self-verifying: refuses to write unless it finds the exact
expected anchor, and exits 0 with "already applied" if the fix is present.

Usage:  python3 fix_fingerprint.py <path-to-harness_lib.py> [--check]
"""
import sys

ANCHOR = """    h = hashlib.sha256()
    for f in scope_files(scope):
        h.update(f.encode())
"""

PATCHED = """    h = hashlib.sha256()
    for f in scope_files(scope):
        # META_ALLOW paths (docs/TASKS.md, .claude/monitor/**, NEEDS_HUMAN.md) are
        # paths EVERY session may write -- harness bookkeeping, never a ticket
        # deliverable, so they can never be evidence about a specific ticket.
        # docs/TASKS.md especially: gen_tasks.py rewrites it AFTER cmd_close
        # computes this hash, which made every receipt whose scope matches docs/**
        # stale the moment it was minted (watchdog P, 2026-08-15).
        if match_any(f, META_ALLOW, raw=True):
            continue
        h.update(f.encode())
"""

MARKER = "if match_any(f, META_ALLOW, raw=True):"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    check_only = "--check" in sys.argv
    src = open(path).read()

    if MARKER in src and "for f in scope_files(scope)" in src:
        print("already applied — no change made")
        return 0
    if ANCHOR not in src:
        print("REFUSING TO PATCH: expected anchor not found in", path)
        print("The function has changed since this patch was written. Re-derive it by hand.")
        return 1
    if src.count(ANCHOR) != 1:
        print("REFUSING TO PATCH: anchor appears %d times, expected exactly 1" % src.count(ANCHOR))
        return 1
    if check_only:
        print("anchor found exactly once — patch would apply cleanly")
        return 0

    open(path, "w").write(src.replace(ANCHOR, PATCHED))
    print("patched", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
