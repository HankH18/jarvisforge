"""T-25: machine-enforce the golden-set human approval gate.

T-7 acceptance 2 requires a human's review of ``backend/evals/golden.jsonl``,
recorded in its ``_meta`` header (``approved_by``/``approved_at``). Before
this ticket, ``evals.run`` never inspected those fields at all -- the CLI
would happily score an unreviewed golden set and exit 0, so the gate was
structurally incapable of checking its own acceptance criterion.

This module tests the fix from BOTH directions, entirely against SYNTHETIC
golden files written under ``tmp_path``:

- approved (``approved_by``/``approved_at`` both non-blank) -> the run
  proceeds normally, no gate error, no draft banner.
- unapproved (missing ``_meta``, a missing key, ``null``, ``""``, or
  whitespace-only) -> ``evals.run.main`` exits non-zero (3) with a loud
  "NOT APPROVED" error on stderr, UNLESS the explicit
  ``--draft-unapproved-not-evidence`` flag is given, in which case the run
  proceeds but is loudly, unmissably labeled as NOT evidence (never a bare
  "PASS:"/"FAIL:" line; the summary dict carries ``draft``/``evidence``
  flags too).

An autouse fixture snapshots the REAL ``backend/evals/golden.jsonl`` before
and after every test in this module and asserts it is byte-identical --
this suite must never approve, edit, or otherwise touch that file; it only
ever writes synthetic fixtures under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# `evals` is a top-level package under backend/, not backend/src/ -- pytest's
# `pythonpath = ["src"]` (pyproject.toml) doesn't reach it. Mirrors how
# backend/evals/run.py resolves backend/src from __file__, and how
# test_golden_ambiguity.py resolves backend/ the same way.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from evals import run  # noqa: E402
from evals.scoring import canonical_approval_bytes, missing_approval_fields  # noqa: E402

_REAL_GOLDEN_PATH = _BACKEND_ROOT / "evals" / "golden.jsonl"

# A single well-formed, structurally-answerable case. `synthesize_grounded`'s
# real user prompt is `json.dumps({"source": ..., "data": ...})` verbatim
# (see jarvis/llm/synthesis.py:_build_user_prompt), and `--client echo`
# mirrors that prompt back as the reply -- so a `must_include` substring that
# lives inside `data` shows up in the echoed answer and scores `pass`, with
# zero fabrications/wrong_refusals/missing_includes/errors. This isolates
# every test in this module to the APPROVAL GATE, not eval scoring.
_CASE_LINE = json.dumps(
    {
        "id": "S-001",
        "query": "What is the synthetic test value?",
        "kind": "grounded",
        "expect": "answer",
        "fixtures": [
            {
                "tool": "synthetic",
                "result": {
                    "source": "synthetic:test",
                    "fetched_at": "2026-01-01T00:00:00+00:00",
                    "data": {"value": "marker-9182"},
                    "error": None,
                },
            }
        ],
        "prefs": [],
        "must_include": ["marker-9182"],
        "must_not_include": ["never-appears-xyz"],
        "note": "T-25 synthetic fixture -- exercises the approval gate only",
    }
)

# T-29: `_write_golden` always writes exactly `_CASE_LINE` as the file's ONE
# non-`_meta` line (see below), so its canonical content hash is fixed and
# computable once, via the EXACT SAME function the gate calls
# (`evals.scoring.canonical_approval_bytes` -- `golden_content_sha256` just
# reads a file and feeds its text through this). Baking the correct hash
# into `_base_meta()`'s default is what lets every EXISTING T-25 test below
# keep passing UNMODIFIED now that the gate also requires a verifiable hash:
# they all go through `_base_meta()`, so they all get one automatically,
# scoped to content that's actually theirs.
_CASE_LINE_SHA256 = hashlib.sha256(canonical_approval_bytes(_CASE_LINE + "\n")).hexdigest()


def _base_meta(**overrides) -> dict:
    meta = {
        "id": "_meta",
        "schema_version": 1,
        "spec_ref": "R6/R6a",
        "approved_by": "Test Reviewer",
        "approved_at": "2026-01-01",
        "approved_sha256": _CASE_LINE_SHA256,
    }
    meta.update(overrides)
    return meta


def _write_golden(tmp_path: Path, meta: dict | None, *, name: str = "golden.jsonl") -> Path:
    """Write a synthetic 1-case golden file under `tmp_path`. `meta=None`
    omits the `_meta` line entirely (the "no header at all" hole);
    otherwise `meta` is JSON-dumped verbatim as line 1.
    """
    path = tmp_path / name
    lines = []
    if meta is not None:
        lines.append(json.dumps(meta))
    lines.append(_CASE_LINE)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(golden: Path, *extra_args: str) -> tuple[int, str, str]:
    """Invoke `evals.run.main` in-process against `golden` with a fixed,
    network-free client/model so nothing but the approval gate is under
    test, capturing (exit_code, stdout, stderr).
    """
    argv = ["--golden", str(golden), "--client", "echo", "--model", "test-model", *extra_args]
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run.main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def _real_golden_jsonl_untouched():
    """The real golden set is T-7's external ground truth (SPEC R6a) --
    nothing in this suite may approve it, edit it, or touch it in any way.
    """
    before = _REAL_GOLDEN_PATH.read_bytes()
    yield
    after = _REAL_GOLDEN_PATH.read_bytes()
    assert after == before, (
        "a test in test_approval_gate.py modified the REAL backend/evals/"
        "golden.jsonl -- T-25 forbids this; only synthetic tmp_path fixtures "
        "may be used"
    )


# ---------------------------------------------------------------------------
# Unit-level: the predicate itself, every enumerated blank-ness hole.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "meta, expected_missing",
    [
        pytest.param(None, ["approved_by", "approved_at"], id="no-meta-line-at-all"),
        pytest.param({"id": "_meta"}, ["approved_by", "approved_at"], id="both-keys-absent"),
        pytest.param(
            {"id": "_meta", "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-key-absent",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank"}, ["approved_at"], id="approved_at-key-absent"
        ),
        pytest.param(
            {"id": "_meta", "approved_by": None, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-null",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank", "approved_at": None},
            ["approved_at"],
            id="approved_at-null",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "", "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-empty-string",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank", "approved_at": ""},
            ["approved_at"],
            id="approved_at-empty-string",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "   ", "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-whitespace-only",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank", "approved_at": "\t\n "},
            ["approved_at"],
            id="approved_at-whitespace-only",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "  ", "approved_at": None},
            ["approved_by", "approved_at"],
            id="both-blank-mixed-forms",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank Holcomb", "approved_at": "2026-08-13"},
            [],
            id="both-present-and-non-blank",
        ),
        # --- non-string / non-blank-but-not-real-value shapes (verifier-found
        # holes: str(0) == "0", str(False) == "False", etc. all read as
        # non-empty under a naive `not str(value).strip()` check) ---
        pytest.param(
            {"id": "_meta", "approved_by": 0, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-int-zero",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": False, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-bool-false",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": True, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-bool-true",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": [], "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-empty-list",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": {}, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-empty-dict",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": {"name": "Hank Holcomb"}, "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-nested-non-empty-object",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": ["Hank", "Holcomb"], "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-non-empty-list",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank Holcomb", "approved_at": 20260813},
            ["approved_at"],
            id="approved_at-int",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank Holcomb", "approved_at": 3.14},
            ["approved_at"],
            id="approved_at-float",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank Holcomb", "approved_at": True},
            ["approved_at"],
            id="approved_at-bool-true",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "---", "approved_at": "2026-01-01"},
            ["approved_by"],
            id="approved_by-punctuation-only",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": "Hank Holcomb", "approved_at": "----"},
            ["approved_at"],
            id="approved_at-punctuation-only",
        ),
        pytest.param(
            {"id": "_meta", "approved_by": 0, "approved_at": False},
            ["approved_by", "approved_at"],
            id="both-non-string",
        ),
    ],
)
def test_missing_approval_fields_matrix(meta, expected_missing):
    assert missing_approval_fields(meta) == expected_missing


# ---------------------------------------------------------------------------
# Direction 1: approved -> the run proceeds (not blocked by the gate).
# ---------------------------------------------------------------------------


def test_approved_synthetic_golden_runs_and_exits_zero(tmp_path):
    golden = _write_golden(tmp_path, _base_meta())
    code, out, err = _run(golden)
    assert code == 0, f"approved synthetic run should pass; stderr={err!r}"
    assert "PASS:" in out
    assert "NOT APPROVED" not in err
    assert "DRAFT" not in out and "DRAFT" not in err


def test_approved_synthetic_golden_summary_marks_evidence_true(tmp_path):
    golden = _write_golden(tmp_path, _base_meta())
    report = tmp_path / "report.json"
    code, _out, _err = _run(golden, "--json", str(report))
    assert code == 0
    summary = json.loads(report.read_text())["summary"]
    assert summary["draft"] is False
    assert summary["evidence"] is True


# ---------------------------------------------------------------------------
# Direction 2: unapproved -> the run is blocked (exit non-zero), enumerating
# every way "blank" can show up.
# ---------------------------------------------------------------------------


def test_no_meta_line_at_all_blocks(tmp_path):
    golden = _write_golden(tmp_path, meta=None)
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err
    assert "approved_by" in err and "approved_at" in err


def test_meta_present_but_approved_by_key_missing_blocks(tmp_path):
    meta = _base_meta()
    del meta["approved_by"]
    golden = _write_golden(tmp_path, meta)
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err
    assert "approved_by" in err


def test_meta_present_but_approved_at_key_missing_blocks(tmp_path):
    meta = _base_meta()
    del meta["approved_at"]
    golden = _write_golden(tmp_path, meta)
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err
    assert "approved_at" in err


def test_approved_by_null_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_by=None))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


def test_approved_at_null_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_at=None))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


def test_approved_by_empty_string_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_by=""))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


def test_approved_at_empty_string_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_at=""))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


def test_approved_by_whitespace_only_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_by="   "))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


def test_approved_at_whitespace_only_blocks(tmp_path):
    golden = _write_golden(tmp_path, _base_meta(approved_at="\t\n  "))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err


GENUINELY_APPROVED_VALUE = "Test Reviewer"

# Every shape a verifier-plausible forged/careless approval value could take.
# `str(value)` is non-empty for ALL of these (`str(0) == "0"`, `str(False) ==
# "False"`, `str([]) == "[]"`, ...), which is exactly the hole a naive
# `not str(value).strip()` blank check misses -- none of these are a `str`
# at all except the last three, and those three have zero alphanumeric
# content, so none of them may read as a real human approval.
_BAD_APPROVAL_VALUES = [
    pytest.param(0, id="int-zero"),
    pytest.param(False, id="bool-false"),
    pytest.param(True, id="bool-true"),
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty-dict"),
    pytest.param({"name": "Hank Holcomb"}, id="nested-non-empty-object"),
    pytest.param(3.14, id="float"),
    pytest.param(None, id="null"),
    pytest.param("", id="empty-string"),
    pytest.param("   ", id="whitespace-only"),
    pytest.param("\t\n ", id="whitespace-only-tabs-newlines"),
    pytest.param("---", id="punctuation-only"),
]


@pytest.mark.parametrize("bad_value", _BAD_APPROVAL_VALUES)
def test_bad_approved_by_shapes_block_via_cli(tmp_path, bad_value):
    """Proof A: every non-real-value shape for `approved_by`, exercised
    through the actual CLI (not just the predicate) -- must be non-zero,
    must print the "NOT APPROVED" error, must never print a draft banner
    (no draft flag was asked for) or any per-case output.
    """
    golden = _write_golden(tmp_path, _base_meta(approved_by=bad_value))
    code, out, err = _run(golden)
    assert code != 0, f"approved_by={bad_value!r} must not pass the gate"
    assert "NOT APPROVED" in err
    assert "approved_by" in err
    assert "DRAFT" not in out and "DRAFT" not in err
    assert out == ""


@pytest.mark.parametrize("bad_value", _BAD_APPROVAL_VALUES)
def test_bad_approved_at_shapes_block_via_cli(tmp_path, bad_value):
    """Proof A, `approved_at` side of the same enumeration."""
    golden = _write_golden(tmp_path, _base_meta(approved_at=bad_value))
    code, out, err = _run(golden)
    assert code != 0, f"approved_at={bad_value!r} must not pass the gate"
    assert "NOT APPROVED" in err
    assert "approved_at" in err
    assert "DRAFT" not in out and "DRAFT" not in err
    assert out == ""


def test_approved_by_valid_approved_at_non_string_blocks(tmp_path):
    """Proof A: one field valid, the OTHER a non-string shape -- the valid
    field must not paper over the bad one."""
    golden = _write_golden(tmp_path, _base_meta(approved_at=20260813))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err
    # The `reason` clause names exactly the missing field(s) as `_meta.<key>`
    # -- only `approved_at` should appear there; `approved_by/approved_at`
    # also shows up together in the generic remediation sentence, so this
    # checks the specific `_meta.<key>` form, not a bare substring match.
    assert "_meta.approved_at" in err
    assert "_meta.approved_by" not in err


def test_approved_at_valid_approved_by_non_string_blocks(tmp_path):
    """Proof A, mirrored: `approved_by` a non-string shape, `approved_at`
    genuinely valid."""
    golden = _write_golden(tmp_path, _base_meta(approved_by=["Hank", "Holcomb"]))
    code, _out, err = _run(golden)
    assert code != 0
    assert "NOT APPROVED" in err
    assert "approved_by" in err


# ---------------------------------------------------------------------------
# Proof B: a golden file with TWO `_meta` lines (a forged approved one ahead
# of the genuine unapproved one) must be REFUSED outright, not resolved by
# silently picking the first (or last) line.
# ---------------------------------------------------------------------------


def _write_golden_with_duplicate_meta(tmp_path: Path, first_meta: dict, second_meta: dict) -> Path:
    path = tmp_path / "golden_dup_meta.jsonl"
    lines = [json.dumps(first_meta), json.dumps(second_meta), _CASE_LINE]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_forged_approved_meta_ahead_of_real_unapproved_meta_is_refused(tmp_path):
    forged_approved = _base_meta()  # looks fully approved
    real_unapproved = _base_meta(approved_by=None, approved_at=None)
    golden = _write_golden_with_duplicate_meta(tmp_path, forged_approved, real_unapproved)

    code, out, err = _run(golden)

    assert code != 0, "a forged approved _meta ahead of the real unapproved one must be refused"
    assert out == ""
    assert "duplicate" in err.lower()
    assert "_meta" in err
    # This must be the file-load refusal, not the ordinary approval-gate
    # error -- the run never got far enough to even ask "is it approved".
    assert "NOT APPROVED" not in err


def test_duplicate_meta_refused_even_when_both_copies_are_approved(tmp_path):
    """Not a judgment call about WHICH copy is right -- any second `_meta`
    line is refused outright, even if both would individually look
    approved."""
    golden = _write_golden_with_duplicate_meta(tmp_path, _base_meta(), _base_meta())
    code, out, err = _run(golden)
    assert code != 0
    assert out == ""
    assert "duplicate" in err.lower()


def test_load_meta_raises_on_duplicate_meta_lines_with_line_numbers(tmp_path):
    from evals.scoring import load_meta

    golden = _write_golden_with_duplicate_meta(
        tmp_path, _base_meta(), _base_meta(approved_by="")
    )
    with pytest.raises(ValueError, match="duplicate _meta header"):
        load_meta(golden)


def test_unapproved_run_never_prints_a_bare_pass_line(tmp_path):
    """A gate that blocks the exit code but still prints "PASS:" would be a
    trap for anything grepping logs -- the gate error path must not emit the
    normal scoring summary at all (the CLI returns before scoring runs).
    """
    golden = _write_golden(tmp_path, meta=None)
    _code, out, _err = _run(golden)
    assert "PASS:" not in out
    assert out == "", "the gate should refuse before printing any per-case output"


# ---------------------------------------------------------------------------
# Bypass enumeration: no flag, env var, or unrelated argument gets past the
# gate except the one sanctioned, self-labeling draft flag.
# ---------------------------------------------------------------------------


def test_unrelated_flags_do_not_bypass_the_gate(tmp_path):
    golden = _write_golden(tmp_path, meta=None)
    code, _out, err = _run(
        golden, "--max-fabrications", "999", "--max-wrong-refusals", "999", "--limit", "0"
    )
    assert code != 0
    assert "NOT APPROVED" in err


@pytest.mark.parametrize(
    "env_name",
    [
        "SKIP_APPROVAL",
        "SKIP_APPROVAL_GATE",
        "JARVIS_SKIP_APPROVAL",
        "EVALS_APPROVED",
        "GOLDEN_APPROVED",
        "APPROVED",
        "FORCE",
        "CI",
    ],
)
def test_no_plausible_env_var_bypasses_the_gate(tmp_path, monkeypatch, env_name):
    monkeypatch.setenv(env_name, "1")
    golden = _write_golden(tmp_path, meta=None)
    code, _out, err = _run(golden)
    assert code != 0, f"env var {env_name}=1 must not bypass the approval gate"
    assert "NOT APPROVED" in err


@pytest.mark.parametrize(
    "bad_flag", ["--force", "--skip-approval", "--yes", "-y", "--i-approve-this"]
)
def test_guessed_bypass_flags_are_rejected_by_argparse(tmp_path, bad_flag):
    golden = _write_golden(tmp_path, meta=None)
    argv = ["--golden", str(golden), "--client", "echo", "--model", "test-model", bad_flag]
    with pytest.raises(SystemExit) as excinfo:
        run.main(argv)
    assert excinfo.value.code == 2  # argparse's own "unrecognized arguments" exit


# ---------------------------------------------------------------------------
# The sanctioned draft escape hatch: explicit, self-labeling, never silent.
# ---------------------------------------------------------------------------


def test_draft_flag_name_and_banner_state_plainly_not_evidence():
    assert "not-evidence" in run._build_argparser().format_help()
    assert "NOT EVIDENCE" in run._DRAFT_BANNER
    assert "NOT APPROVED" in run._DRAFT_BANNER


def test_draft_run_proceeds_but_is_unmissably_labeled(tmp_path):
    golden = _write_golden(tmp_path, meta=None)
    code, out, err = _run(golden, "--draft-unapproved-not-evidence")
    # Draft mode carves the unapproved-golden-set exit out of the "always
    # nonzero" rule (acceptance 1) -- our synthetic case scores cleanly, so
    # 0 is the legitimate outcome here. What must hold is that the output
    # can never be mistaken for real evidence.
    assert code == 0
    assert "PASS:" not in out
    assert "FAIL:" not in out
    assert "DRAFT-NOT-EVIDENCE" in out
    assert "NOT EVIDENCE" in out
    assert "NOT EVIDENCE" in err
    assert err.count(run._DRAFT_BANNER) == 2, "banner must print at both start and end"


def test_draft_run_summary_marks_evidence_false(tmp_path):
    golden = _write_golden(tmp_path, meta=None)
    report = tmp_path / "report.json"
    code, _out, _err = _run(golden, "--draft-unapproved-not-evidence", "--json", str(report))
    assert code == 0
    summary = json.loads(report.read_text())["summary"]
    assert summary["draft"] is True
    assert summary["evidence"] is False


def test_draft_flag_is_a_noop_when_already_approved(tmp_path):
    """Draft mode is the escape hatch for an UNAPPROVED set; passing it
    against an already-approved set must not relabel a real result as
    draft (there's nothing to draft around)."""
    golden = _write_golden(tmp_path, _base_meta())
    code, out, err = _run(golden, "--draft-unapproved-not-evidence")
    assert code == 0
    assert "PASS:" in out
    assert "DRAFT" not in out and "DRAFT" not in err


def test_require_approved_overrides_the_draft_flag(tmp_path):
    """--require-approved (what T-7's verify command sets) must win even if
    --draft-unapproved-not-evidence is also passed -- a gate/CI invocation
    can never be talked into accepting draft output."""
    golden = _write_golden(tmp_path, meta=None)
    code, out, err = _run(
        golden, "--draft-unapproved-not-evidence", "--require-approved"
    )
    assert code != 0
    assert "NOT APPROVED" in err
    assert "--require-approved" in err
    assert out == ""


# ---------------------------------------------------------------------------
# The real, already-approved golden set: still runs, still untouched.
# ---------------------------------------------------------------------------


def test_real_golden_jsonl_is_currently_approved():
    from evals.scoring import load_meta

    meta = load_meta(_REAL_GOLDEN_PATH)
    assert missing_approval_fields(meta) == [], (
        "the real golden.jsonl is expected to already carry a valid "
        "approved_by/approved_at (Hank Holcomb, 2026-08-13) -- if this "
        "fails, someone blanked the real approval, which this ticket must "
        "never do itself"
    )


def test_real_golden_jsonl_passes_the_gate_with_echo_client():
    """The new gate must not block the real, already-approved golden set --
    only reads it, never writes it (enforced separately by the autouse
    fixture above)."""
    code, out, err = _run(_REAL_GOLDEN_PATH)
    assert code != 3, "the approval gate itself must not be what fails this run"
    assert "NOT APPROVED" not in err
    assert "DRAFT" not in out and "DRAFT" not in err
    assert "PASS:" in out or "FAIL:" in out, "must reach the normal scoring summary"


def test_real_golden_jsonl_approved_sha256_matches_its_current_content():
    """T-29: the real golden.jsonl's `_meta.approved_sha256` was minted for
    its content at close time (a one-time, mechanical edit -- see
    `evals/print_approval_hash.py`) and must still verify. If this fails,
    either golden.jsonl's case content changed without a re-approval, or
    the hash was pasted wrong -- either way that is exactly the T-19 defect
    T-29 exists to catch, so this must never be "fixed" by editing the
    hash without a human re-reviewing the content it now covers."""
    from evals.scoring import approval_hash_problem, load_meta

    meta = load_meta(_REAL_GOLDEN_PATH)
    assert approval_hash_problem(meta, _REAL_GOLDEN_PATH) is None


# ---------------------------------------------------------------------------
# T-29: approval binds to the bytes it approved. `approved_by`/`approved_at`
# (T-25, above) prove a human's NAME and DATE are recorded; these tests prove
# the SEPARATE, additional axis -- that the recorded approval actually covers
# the CURRENT content -- is enforced too: a golden set can be "approved" by
# name while `approved_sha256` is missing, blank, malformed, or simply wrong
# for what's on disk, and the gate must refuse all of those exactly like an
# unverified approval, per the acceptance text ("unverifiable is never
# current"), with a distinct exit code (4, never 3 -- that code is
# `missing_approval_fields`'s alone) and remedy text naming the fix.
# ---------------------------------------------------------------------------


def test_approved_sha256_matching_content_runs_and_exits_zero(tmp_path):
    """Direction 1 (match): a well-formed, CORRECT hash is a no-op -- the
    run proceeds exactly as it did before T-29."""
    golden = _write_golden(tmp_path, _base_meta())
    code, out, err = _run(golden)
    assert code == 0, f"a correct approved_sha256 must not block the run; stderr={err!r}"
    assert "PASS:" in out
    assert "T-29" not in err and "content" not in err.lower()


@pytest.mark.parametrize(
    "bad_hash",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param(0, id="int-zero"),
        pytest.param(True, id="bool-true"),
        pytest.param([], id="empty-list"),
        pytest.param("not-a-hash", id="too-short-not-hex"),
        pytest.param("a" * 63, id="one-char-short-of-64"),
        pytest.param("g" * 64, id="64-chars-but-not-hex"),
    ],
)
def test_missing_or_malformed_approved_sha256_blocks_an_otherwise_approved_set(
    tmp_path, bad_hash
):
    """Direction 2 (missing): every shape that isn't a well-formed sha256
    hex digest is UNVERIFIABLE, and unverifiable is never current -- this
    must block exactly like a wrong hash would, distinctly from the T-25
    approved_by/approved_at gate (exit 3), and print no per-case output."""
    golden = _write_golden(tmp_path, _base_meta(approved_sha256=bad_hash))
    code, out, err = _run(golden)
    assert code == 4, f"missing/malformed approved_sha256 must exit 4; got {code}, stderr={err!r}"
    assert code != 3, "must not be conflated with the approved_by/approved_at gate"
    assert "NOT APPROVED" not in err, "this is a distinct failure from T-25's gate"
    assert "approved_sha256" in err
    assert "print_approval_hash" in err, "remedy must name the helper"
    assert out == "", "the gate should refuse before printing any per-case output"


def test_approved_sha256_key_entirely_absent_blocks(tmp_path):
    """The "no key at all" hole, mirroring T-25's own `del meta["approved_by"]`
    coverage: approved_by/approved_at present and valid, but
    `approved_sha256` was simply never added to `_meta`."""
    meta = _base_meta()
    del meta["approved_sha256"]
    golden = _write_golden(tmp_path, meta)
    code, out, err = _run(golden)
    assert code == 4
    assert "approved_sha256" in err
    assert out == ""


def test_approved_sha256_wrong_but_well_formed_hash_blocks_as_mismatch(tmp_path):
    """Direction 2 (mismatch): a well-formed sha256 hex digest that simply
    isn't THIS content's hash -- e.g. copied from a previous approval and
    never updated after an edit, the exact T-19 failure mode this ticket
    exists to close."""
    wrong_but_well_formed = hashlib.sha256(b"not the approved content").hexdigest()
    golden = _write_golden(tmp_path, _base_meta(approved_sha256=wrong_but_well_formed))
    code, out, err = _run(golden)
    assert code == 4
    assert code != 3
    assert "NOT APPROVED" not in err
    assert "CHANGED SINCE APPROVAL" in err or "does not match" in err
    assert "print_approval_hash" in err
    assert out == ""


def test_approved_sha256_mismatch_is_case_insensitive_on_the_correct_value(tmp_path):
    """Uppercase hex is still a MATCH (a human may paste either case) -- this
    is not a mismatch case, it proves the comparison itself is
    case-insensitive rather than accidentally over-strict."""
    golden = _write_golden(tmp_path, _base_meta(approved_sha256=_CASE_LINE_SHA256.upper()))
    code, out, err = _run(golden)
    assert code == 0, f"uppercase hex of the correct hash must still verify; stderr={err!r}"
    assert "PASS:" in out


def test_missing_hash_check_is_not_reached_when_approved_by_at_are_already_bad(tmp_path):
    """The hash check only fires on an OTHERWISE-approved set (acceptance 1's
    own wording) -- when approved_by/approved_at are already missing, the
    T-25 gate (exit 3) must still be what blocks, not a hash complaint,
    even though this synthetic file also happens to have no
    approved_sha256 at all."""
    meta = _base_meta(approved_by=None)
    del meta["approved_sha256"]
    golden = _write_golden(tmp_path, meta)
    code, out, err = _run(golden)
    assert code == 3
    assert "NOT APPROVED" in err
    assert "approved_sha256" not in err
