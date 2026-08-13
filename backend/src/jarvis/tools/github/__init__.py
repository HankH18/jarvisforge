"""GitHub Q&A tool package (T-4; docs/DESIGN.md §Interfaces).

Two tools, both `heavy=True`:
  - ``load_repo_digest``: whole-repo digest via gitingest (digest.py).
  - ``github_query``: targeted GraphQL lookups via githubkit (query.py).
"""

from __future__ import annotations

from .digest import GITHUB_DIGEST_TOOL, load_repo_digest
from .query import GITHUB_QUERY_TOOL, github_query

__all__ = [
    "GITHUB_DIGEST_TOOL",
    "GITHUB_QUERY_TOOL",
    "load_repo_digest",
    "github_query",
]
