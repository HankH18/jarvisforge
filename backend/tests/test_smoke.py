"""Bootstrap smoke test: the harness runs and the package layout imports.

T-0 ships no app logic, so this is the only test in the suite. Its job is to
prove `uv run pytest` is a working gate before any feature ticket relies on it.
"""

import importlib

import jarvis

# Pinned in docs/DESIGN.md §Architecture. Feature tickets fill these in; if one
# is renamed or dropped, that is a plan defect and this test should catch it.
PINNED_SUBPACKAGES = [
    "pipeline",
    "server",
    "llm",
    "tools",
    "memory",
    "cadence",
    "auth",
    "agents_pr",
]


def test_package_imports():
    assert jarvis.__version__ == "0.1.0"


def test_pinned_subpackages_exist():
    for name in PINNED_SUBPACKAGES:
        assert importlib.import_module(f"jarvis.{name}") is not None
