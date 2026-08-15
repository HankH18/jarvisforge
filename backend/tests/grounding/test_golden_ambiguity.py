"""T-19: a scripted guard against the G-004 class of bug.

G-004 asked the chance of rain on 2026-08-18 with ``must_include: ["27"]``,
but "27" was a substring of TWO distinct facts in its own fixture:
``precipitation_probability_max[5] == 27`` (the correct answer, Aug 18) and
``temperature_2m_max[4] == 27.0`` (a different field, a different day, Aug
17). Because scoring (``evals.scoring.score``) is a naive case-insensitive
substring check against the model's answer text, an answer citing the WRONG
fact could still satisfy ``must_include`` and score ``pass`` -- a golden
case that can't tell a right answer from a wrong one is not actually
testing anything.

This walks every case's fixtures and fails loudly if a ``must_include``
substring is satisfiable by more than one DISTINCT field. The default rule
is literal, per the acceptance text: a match under one dict-key path (a
"field", indices aside -- the same field repeated at different array
positions, e.g. several days that both happen to read 0.0mm of rain, is
still one field, not two) is a different fact from a match under any OTHER
field path, full stop, EVEN if both fields live in the same record (the
same list element). An earlier version of this checker exempted anything
sharing an enclosing list index ("same record"), which was too broad: it
missed the case of two unrelated fields in the same record coincidentally
sharing a value (e.g. a record with both `count: 42` and `total: 42` --
two different facts, not a restatement).

The ONE exemption is narrow, explicit, and IDENTITY-based (not name-based):
`_ALIASED_FIELD_PAIRS` names the one field-NAME pair eligible at all
(`number`/`url`), but naming alone is not enough to exempt a match -- a
first cut of this checker merged any same-record `number`/`url` pair purely
because of their names, and a verifier demonstrated the false exemption
that invites: a record with an unrelated `number` and an unrelated `url`
that merely happen to share digits gets waved through with no relationship
between their actual values. The exemption now additionally requires that
the `number` field's own value equal the `url` field's own value's
IDENTIFYING path segment -- the LAST path segment (ignoring a trailing
slash, query string, or fragment) -- e.g. `10829` in `.../pull/10829`,
matching how GitHub actually structures PR/issue URLs.

T-29 (verifier finding): an EARLIER version of this exemption accepted the
number as ANY `/`-bounded path segment of the url, not specifically its
LAST/identifying one. That is too broad in a different way than the
name-only bug above: `number=42` against a url like
`.../orgs/42/repo/pull/999` would wrongly exempt the pair, because `42`
does occur as its own bounded segment (an org id) even though the url's
actual identifying segment -- its PR number -- is `999`, a different value
entirely. Requiring the number to equal the LAST segment specifically
closes this: `10829` in `.../pull/10829` (its own last segment) is still
exempted, but `42` in `.../orgs/42/repo/pull/999` (last segment `999`) is
not, and neither is `42` buried inside an unrelated segment like
`.../repo42/issues/999` (last segment `999` there too).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# `evals` is a top-level package under backend/, not backend/src/ -- pytest's
# `pythonpath = ["src"]` (pyproject.toml) doesn't reach it, and this is the
# only test module that needs it, so resolve and insert backend/ here
# (mirrors how backend/evals/run.py resolves backend/src from __file__).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from evals.scoring import GoldenCase, load_golden  # noqa: E402

_GOLDEN_PATH = _BACKEND_ROOT / "evals" / "golden.jsonl"

# The ONLY pair of field NAMES eligible for the exemption at all. Naming
# alone is NOT sufficient -- see `_identifier_restated_in_url` below, which
# is the actual gate.
_ALIASED_FIELD_PAIRS: frozenset[frozenset[str]] = frozenset({frozenset({"number", "url"})})


def _identifier_restated_in_url(number_value: str, url_value: str) -> bool:
    """True iff ``number_value`` equals ``url_value``'s IDENTIFYING path
    segment -- the LAST path segment, ignoring a trailing slash and any
    query string (``?...``) or fragment (``#...``) -- e.g. ``10829`` in
    ``https://github.com/.../pull/10829``.

    A bare substring check is NOT enough: ``42`` is a substring of
    ``.../repo42/issues/999`` too, but the identifying segment there is
    ``999``, so that must NOT count as the same fact. Neither is matching
    ANY ``/``-bounded segment enough (T-29, verifier finding): ``42`` is
    its own bounded segment in ``.../orgs/42/repo/pull/999``, but it is an
    unrelated org id, not the url's actual identifier (``999``) -- only the
    LAST segment identifies a GitHub PR/issue, so only that one counts.
    """
    path = url_value.split("?", 1)[0].split("#", 1)[0]
    last_segment = path.rstrip("/").rsplit("/", 1)[-1]
    return last_segment == number_value


def _leaf_matches(
    data: Any, needle: str, keys: tuple = (), path: tuple = (), record: tuple = ()
) -> list[tuple[tuple, tuple, str | None, str]]:
    """Every ``(field_id, record, field_name, value)`` under ``data`` whose
    leaf value contains ``needle`` (case-insensitive).

    - ``field_id``: the dict-KEY path only (list indices stripped) --
      identifies "which field", ignoring array position, so the same field
      repeated at different indices collapses to one identity.
    - ``record``: the path up to and including the nearest enclosing list
      index -- identifies "which array element", used ONLY to scope the
      narrow alias exemption above, never as a blanket grouping.
    - ``field_name``: ``field_id[-1]``, the leaf's immediate key name.
    - ``value``: the leaf's own stringified value, needed to verify the
      alias exemption is actually value-justified, not just name-justified.
    """
    matches: list[tuple[tuple, tuple, str | None, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            matches.extend(_leaf_matches(value, needle, keys + (key,), path + (key,), record))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            new_record = path + (i,)
            matches.extend(_leaf_matches(item, needle, keys, path + (str(i),), new_record))
    else:
        if needle.lower() in str(data).lower():
            field_name = keys[-1] if keys else None
            matches.append((keys, record, field_name, str(data)))
    return matches


def _ambiguous_pins(case: GoldenCase) -> dict[str, list[tuple]]:
    """``{must_include substring: [distinct field_ids it matches]}`` for
    every substring satisfiable by more than one distinct field in
    ``case``'s own fixtures, after applying the narrow, identity-checked
    alias exemption.
    """
    problems: dict[str, list[tuple]] = {}
    for needle in case.must_include:
        raw_matches: list[tuple[tuple, tuple, str | None, str]] = []
        for fixture in case.fixtures:
            raw_matches.extend(_leaf_matches(fixture.get("data"), needle))

        field_ids = {m[0] for m in raw_matches}
        if len(field_ids) <= 1:
            continue  # one field (however many positions) -- never ambiguous

        # Union-find over field_ids, merging two ONLY when an alias-eligible
        # pair of matches shares a record AND the number's value is actually
        # restated in the url's value (not merely same-named).
        parent: dict[tuple, tuple] = {fid: fid for fid in field_ids}

        def find(x: tuple) -> tuple:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: tuple, b: tuple) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, (field_a, record_a, name_a, value_a) in enumerate(raw_matches):
            for field_b, record_b, name_b, value_b in raw_matches[i + 1 :]:
                if field_a == field_b:
                    continue
                if record_a != record_b:
                    continue
                if name_a is None or name_b is None:
                    continue
                if frozenset({name_a, name_b}) not in _ALIASED_FIELD_PAIRS:
                    continue
                number_value, url_value = (value_a, value_b) if name_a == "number" else (
                    value_b,
                    value_a,
                )
                if _identifier_restated_in_url(number_value, url_value):
                    union(field_a, field_b)

        distinct_roots = {find(fid) for fid in field_ids}
        if len(distinct_roots) > 1:
            problems[needle] = sorted(field_ids, key=str)
    return problems


_CASES = load_golden(_GOLDEN_PATH)


@pytest.mark.parametrize("case", _CASES, ids=[c.id for c in _CASES])
def test_must_include_pins_are_unambiguous_within_their_own_fixture(case: GoldenCase) -> None:
    problems = _ambiguous_pins(case)
    assert not problems, (
        f"{case.id}: must_include substring(s) are satisfiable by more than one "
        f"distinct field in this case's own fixture, so an answer citing the WRONG "
        f"field could still score pass: {problems}"
    )


def test_ambiguity_detector_actually_catches_the_g004_bug() -> None:
    """Proves the detector isn't vacuous: the exact pre-fix G-004 shape (a
    bare '27' pinned against a fixture where precipitation_probability_max[5]
    and temperature_2m_max[4] both equal 27) is flagged.
    """
    case = GoldenCase(
        id="G-004-PRE-FIX-REPRO",
        query="What's the chance of rain in London on August 18, 2026?",
        kind="grounded",
        expect="answer",
        fixtures=[
            {
                "source": "weather:open-meteo",
                "data": {
                    "daily": {
                        "precipitation_probability_max": [0, 2, 25, 22, 12, 27, 10],
                        "temperature_2m_max": [35.2, 32.7, 25.7, 24.4, 27.0, 26.6, 25.0],
                    }
                },
                "error": None,
            }
        ],
        prefs=[],
        must_include=["27"],
        must_not_include=["45"],
        note="pre-fix repro",
    )
    problems = _ambiguous_pins(case)
    assert "27" in problems, "detector failed to flag the known G-004 collision"
    assert len(problems["27"]) > 1


def test_ambiguity_detector_flags_same_record_cross_field_collisions() -> None:
    """T-19 defect 2 (verifier finding): two DIFFERENT fields inside the
    SAME record that coincidentally share a value must be flagged -- the
    old record-wide exemption swallowed this. `count` and `total` are not
    an aliased pair, so this must fail even though both live in one record.
    """
    case = GoldenCase(
        id="SYNTHETIC-SAME-RECORD-CROSS-FIELD",
        query="synthetic",
        kind="grounded",
        expect="answer",
        fixtures=[
            {
                "source": "synthetic:test",
                "data": {"record": {"count": 42, "total": 42}},
                "error": None,
            }
        ],
        prefs=[],
        must_include=["42"],
        must_not_include=[],
        note="synthetic same-record cross-field collision (not the number/url alias)",
    )
    problems = _ambiguous_pins(case)
    assert "42" in problems, "detector failed to flag a same-record count/total collision"
    assert len(problems["42"]) > 1


def test_ambiguity_detector_still_exempts_the_named_number_url_alias() -> None:
    """The one legitimate exemption must still hold: a PR/issue's `number`
    restated inside its own `url` is one fact, not two -- this must NOT be
    flagged (this is the G-012/G-014 shape).
    """
    case = GoldenCase(
        id="SYNTHETIC-NUMBER-URL-ALIAS",
        query="synthetic",
        kind="grounded",
        expect="answer",
        fixtures=[
            {
                "source": "synthetic:test",
                "data": {
                    "nodes": [
                        {"number": 10829, "url": "https://example.com/pull/10829"},
                        {"number": 10827, "url": "https://example.com/pull/10827"},
                    ]
                },
                "error": None,
            }
        ],
        prefs=[],
        must_include=["10829"],
        must_not_include=[],
        note="synthetic number/url alias, same record",
    )
    problems = _ambiguous_pins(case)
    assert not problems, f"legitimate number/url restatement was flagged: {problems}"


def test_ambiguity_detector_does_not_wrongly_exempt_unrelated_number_and_url() -> None:
    """T-19 residual gap (verifier finding): matching by field NAME alone
    ("number" next to "url" in the same record) is not sufficient -- a
    `number` whose value is merely a coincidental digit run somewhere inside
    an unrelated `url` (not its own path segment) must still be flagged.
    Here `number` is 42 and the real identifier in the url's path is 999;
    "42" only appears buried inside the unrelated segment "repo42".
    """
    case = GoldenCase(
        id="SYNTHETIC-UNRELATED-NUMBER-URL",
        query="synthetic",
        kind="grounded",
        expect="answer",
        fixtures=[
            {
                "source": "synthetic:test",
                "data": {
                    "record": {
                        "number": 42,
                        "url": "https://example.com/repo42/issues/999",
                    }
                },
                "error": None,
            }
        ],
        prefs=[],
        must_include=["42"],
        must_not_include=[],
        note="synthetic: same-named number/url pair, unrelated values -- must still be flagged",
    )
    problems = _ambiguous_pins(case)
    assert "42" in problems, (
        "detector wrongly exempted a number/url pair by NAME alone, with no "
        "value relationship between them"
    )
    assert len(problems["42"]) > 1


def test_ambiguity_detector_requires_the_identifying_segment_not_any_segment() -> None:
    """T-29 (verifier finding): the number must equal the url's IDENTIFYING
    (last) path segment, not merely occur as ANY `/`-bounded segment
    somewhere in the url. Here `number` is 42, which DOES occur as its own
    bounded segment (an unrelated org id) in
    `.../orgs/42/repo/pull/999` -- an earlier version of this exemption
    (matching any bounded segment) wrongly waved this through. The url's
    actual identifying segment is `999`, a completely different value, so
    this is a genuine cross-field collision and must be flagged.

    This test fails red against the old any-segment rule -- confirmed by
    temporarily reverting `_identifier_restated_in_url` to the old
    `re.search(r"(?:^|/)" + re.escape(number_value) + r"(?:$|/)", url_value)`
    check and observing this assertion fail, before restoring the fix.
    """
    case = GoldenCase(
        id="SYNTHETIC-NUMBER-MATCHES-NON-IDENTIFYING-SEGMENT",
        query="synthetic",
        kind="grounded",
        expect="answer",
        fixtures=[
            {
                "source": "synthetic:test",
                "data": {
                    "record": {
                        "number": 42,
                        "url": "https://example.com/orgs/42/repo/pull/999",
                    }
                },
                "error": None,
            }
        ],
        prefs=[],
        must_include=["42"],
        must_not_include=[],
        note=(
            "synthetic: number matches a non-identifying (org-id) url segment, "
            "not the url's actual identifying (last) segment -- must still be "
            "flagged, not exempted by the number/url alias"
        ),
    )
    problems = _ambiguous_pins(case)
    assert "42" in problems, (
        "detector wrongly exempted a number/url pair where the number matched "
        "a non-identifying path segment instead of the url's actual "
        "identifying (last) segment"
    )
    assert len(problems["42"]) > 1
