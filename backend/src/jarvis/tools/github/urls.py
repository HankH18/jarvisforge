"""GitHub repo URL parsing shared by both T-4 tools.

Deliberately narrow: only ``http(s)://github.com/<owner>/<repo>[...]`` URLs
resolve. Anything else (malformed strings, other hosts like gitlab.com or
raw.githubusercontent.com, missing owner/repo) returns ``None`` so callers
can fold it into ``ToolResult.error`` -- never a guess.
"""

from __future__ import annotations

from urllib.parse import urlparse


def parse_github_url(url: str) -> tuple[str, str] | None:
    """``url`` -> ``(owner, repo)``, or ``None`` if it isn't a github.com
    repo URL. A trailing ``.git`` suffix and extra path segments (e.g.
    ``/tree/main``) are tolerated and ignored.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() != "github.com":
        return None

    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        return None
    return owner, repo
