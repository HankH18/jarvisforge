#!/usr/bin/env python3
"""Receipt fingerprint drift report -- and WHY each receipt drifted.

WATCHDOG.md tells the watchdog to recompute each receipt's fingerprint and treat a
mismatch as post-verification drift. That instruction is only useful if the signal is
clean. Today it is not: there are two very different causes of a mismatch, and the raw
MATCH/DRIFT verdict cannot tell them apart.

  BOOKKEEPING -- the only changed files are META_ALLOW paths (docs/TASKS.md,
                 .claude/monitor/**, NEEDS_HUMAN.md). Harness exhaust, not evidence
                 of anything. gen_tasks.py rewrites docs/TASKS.md AFTER cmd_close
                 computes the hash, so this fires on the harness's own close.
                 -> fixed by .claude/monitor/fix_fingerprint.py

  SCOPE OVERLAP -- a LATER ticket legitimately edited a real file inside this
                 ticket's broad declared scope (docs/**, scripts/**). Not tampering,
                 but it means a whole-scope fingerprint can never stay stable in a
                 plan where scopes overlap. Needs a design decision, not a patch:
                 fingerprint the files the ticket actually CHANGED, not its whole glob.

  CONTENT     -- a file changed that is neither of the above. THIS is the case the
                 mechanism exists to catch. Look at it.

Read-only. Touches nothing. Run from the repo root:
    python3 .claude/monitor/check_fingerprint_drift.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import harness_lib as h  # noqa: E402

EVID = os.path.join(h.ROOT, ".claude", "evidence")


def changed_scope_files(close_commit, scope):
    out = subprocess.run(
        ["git", "diff", "--name-only", close_commit, "HEAD"],
        capture_output=True, text=True, cwd=h.ROOT).stdout.split()
    return [f for f in out if h.match_any(f, scope)]


def main():
    if not os.path.isdir(EVID):
        print("no .claude/evidence/ directory")
        return 0
    receipts = sorted(f for f in os.listdir(EVID) if f.endswith(".json"))
    if not receipts:
        print("no .json receipts")
        return 0

    worst = 0
    print(f"{'ticket':<8} {'closed':<9} {'verdict':<9} cause")
    print("-" * 72)
    for fn in receipts:
        r = json.load(open(os.path.join(EVID, fn)))
        tid = r["ticket"]
        t = h.ticket(tid)
        if not t:
            print(f"{tid:<8} {'?':<9} {'NO-TICKET':<9} receipt exists but no such ticket in the plan")
            worst = max(worst, 2)
            continue

        now = h.fingerprint(t["scope"])
        if now == r.get("fingerprint"):
            print(f"{tid:<8} {r['commit'][:8]:<9} {'MATCH':<9} -")
            continue

        changed = changed_scope_files(r["commit"], t["scope"])
        meta = [f for f in changed if h.match_any(f, h.META_ALLOW, raw=True)]
        real = [f for f in changed if f not in meta]

        if changed and not real:
            cause, sev = "BOOKKEEPING", 1
            detail = ", ".join(meta)
        elif real:
            cause, sev = "SCOPE-OVERLAP", 1
            detail = ", ".join(real[:4]) + (f" (+{len(real) - 4} more)" if len(real) > 4 else "")
        else:
            cause, sev = "CONTENT", 2
            detail = "no committed scope file changed -- uncommitted or untracked edits. INVESTIGATE."
        worst = max(worst, sev)
        print(f"{tid:<8} {r['commit'][:8]:<9} {'DRIFT':<9} {cause}: {detail}")

    print()
    print("BOOKKEEPING drift  -> run .claude/monitor/fix_fingerprint.py (future receipts only;")
    print("                      receipts already minted keep their old-rule hash and will not match).")
    print("SCOPE-OVERLAP drift-> design decision: fingerprint changed files, not the whole scope glob.")
    print("CONTENT drift      -> the real signal. Investigate before trusting the receipt.")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
