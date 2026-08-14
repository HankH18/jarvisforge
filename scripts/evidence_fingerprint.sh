#!/usr/bin/env bash
# evidence_fingerprint.sh (T-22) - sourced (not executed) by verify_gate.sh
# and check_evidence_staleness.sh. Binds evidence to CONTENT instead of
# commit position: a fingerprint is a single deterministic token over the
# (path, mode, byte-hash) of every file matching a ticket's declared scope
# globs.
#
# MODE IS PART OF THE FINGERPRINT (not just byte content): `git hash-object`
# only ever hashes blob bytes, never the executable bit, so a bare
# byte-hash fingerprint cannot see a `chmod +x`/`chmod -x` on a scope file
# even after it's committed -- silent green on exactly the kind of change
# this repo's own *.sh files (gated on being executable) care about. Every
# path in the listing is recorded as "path<TAB>mode<TAB>hash", where mode
# is git's own two-value model for regular files ("100644" or "100755";
# "120000" for a symlink) -- deliberately NOT full POSIX permission bits,
# to stay identical to what `git ls-tree` already reports for a committed
# file and avoid stat(1) flag differences between BSD and GNU.
#
# GITIGNORED FILES INSIDE A TICKET'S SCOPE ARE INVISIBLE TO THIS FINGERPRINT
# ON PURPOSE (build artifacts, backend/var/ runtime output, etc. must not
# be hashed), but that exclusion is silent by construction -- editing a
# gitignored file inside a scope glob changes nothing here, which looks
# identical to "nothing changed" unless something says otherwise.
# `scope_has_ignored_files` below exists so check_evidence_staleness.sh can
# disclose that hole explicitly, per ticket, instead of leaving it
# indistinguishable from a clean CURRENT/STALE verdict.
#
# Two functions:
#   fingerprint_live <glob>...        - the CURRENT WORKING TREE. This is
#     what verify_gate.sh records at verify time -- it captures exactly the
#     bytes AND mode the verify command ran against, including any change
#     that is only staged or only sitting in the working tree (not yet
#     committed). This is also what check_evidence_staleness.sh recomputes
#     "now" to compare against a receipt.
#   scope_has_ignored_files <glob>... - prints the gitignored paths (if
#     any) inside the given scope globs, one per line. Empty output means
#     the hole doesn't apply to this ticket.
#
# There is deliberately NO "fingerprint a historical commit and treat that
# as a stand-in for a receipt that never recorded one" function here
# anymore. An earlier version had one and check_evidence_staleness.sh used
# it to infer a baseline for legacy (pre-fingerprint) receipts by picking
# the commit "closest in time" to the receipt's bare timestamp -- provably
# unsound: a same-day reopen can land an unverified regression closer in
# time than the real completion, and the checker would report a genuinely
# stale receipt as CURRENT. A receipt with no recorded fingerprint cannot
# be rehabilitated after the fact by guessing one; see
# check_evidence_staleness.sh's UNVERIFIABLE status instead.
#
# Requires: caller has already `cd`'d to the repo root before sourcing/
# calling. No side effects; pure functions of the working tree / git object
# store. Must never be executed directly (assertions below just define
# functions when sourced).

fp_hash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  else
    cksum | awk '{print $1"-"$2}'
  fi
}

file_mode() {
  # file_mode <path> -> prints git's mode vocabulary for a working-tree
  # path: 120000 for a symlink, 100755 for an executable regular file,
  # 100644 otherwise. Using -x/-L instead of stat(1) sidesteps BSD-vs-GNU
  # stat flag differences entirely.
  local f="$1"
  if [ -L "$f" ]; then
    printf '120000'
  elif [ -x "$f" ]; then
    printf '100755'
  else
    printf '100644'
  fi
}

fingerprint_live() {
  # fingerprint_live <glob>... -> prints one token to stdout.
  local pathspecs=()
  local g
  for g in "$@"; do
    [ -n "$g" ] && pathspecs+=(":(glob)${g}")
  done
  if [ "${#pathspecs[@]}" -eq 0 ]; then
    printf 'EMPTY-SCOPE'
    return 0
  fi

  local files
  files="$(git ls-files --cached --others --exclude-standard -- "${pathspecs[@]}" 2>/dev/null | LC_ALL=C sort -u)"
  if [ -z "$files" ]; then
    printf 'EMPTY-SCOPE'
    return 0
  fi

  local listing="" f h m
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if [ -L "$f" ]; then
      # Match what git actually stores for a symlink: the link TARGET text
      # as blob content, not the bytes of whatever it points to.
      h="$(readlink "$f" 2>/dev/null | git hash-object --stdin 2>/dev/null)"
      [ -z "$h" ] && h="UNREADABLE"
      m="120000"
    elif [ -f "$f" ]; then
      h="$(git hash-object -- "$f" 2>/dev/null)"
      [ -z "$h" ] && h="UNREADABLE"
      m="$(file_mode "$f")"
    else
      h="MISSING"
      m="MISSING"
    fi
    listing="${listing}${f}"$'\t'"${m}"$'\t'"${h}"$'\n'
  done <<<"$files"

  printf '%s' "$listing" | fp_hash
}

scope_has_ignored_files() {
  # scope_has_ignored_files <glob>... -> prints each gitignored path
  # (untracked AND ignored) inside the given scope globs, one per line.
  # No output means this ticket's scope has no such hole right now.
  local pathspecs=()
  local g
  for g in "$@"; do
    [ -n "$g" ] && pathspecs+=(":(glob)${g}")
  done
  [ "${#pathspecs[@]}" -eq 0 ] && return 0
  git ls-files --others --ignored --exclude-standard -- "${pathspecs[@]}" 2>/dev/null
}
