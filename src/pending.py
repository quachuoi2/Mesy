"""Pending unmapped Claude Code projects — single shared file in the repo.

Format (single file at ``<repo>/claude/projects/.pending``):

    source: <hostname>
    discovered: <iso-utc>

    Add a mapping in projects.ini:
      [YourProjectName]
      paths = <raw_name_1>

      [YourProjectName]
      paths = <raw_name_2>

The header records the most recent discovery (host + timestamp).  Each
unmapped project contributes one ``[YourProjectName] / paths = <raw>``
snippet, deduplicated by raw name.  When a mapping is added in
projects.ini, ``prune_pending`` removes the matching snippet; when no
snippets remain the file is deleted.
"""

from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from pathlib import Path

PENDING_RELATIVE = Path("claude") / "projects" / ".pending"

_PATHS_LINE = re.compile(r"^\s*paths\s*=\s*(\S+)\s*$", re.MULTILINE)


def pending_path(repo_base: Path) -> Path:
    return repo_base / PENDING_RELATIVE


def _parse_raw_names(text: str) -> list[str]:
    """Extract raw names (preserving order, deduped) from snippet ``paths =`` lines."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _PATHS_LINE.finditer(text):
        raw = match.group(1)
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _render(raw_names: list[str], hostname: str, now_iso: str) -> str:
    snippets = "\n\n".join(
        f"  [YourProjectName]\n  paths = {raw}" for raw in raw_names
    )
    return (
        f"source: {hostname}\n"
        f"discovered: {now_iso}\n"
        f"\n"
        f"Add a mapping in projects.ini:\n"
        f"{snippets}\n"
    )


def _rewrite(path: Path, raw_names: list[str]) -> None:
    path.write_text(_render(
        raw_names,
        hostname=socket.gethostname(),
        now_iso=datetime.now(timezone.utc).isoformat(),
    ))


def add_pending(repo_base: Path, raw_name: str) -> bool:
    """Record an unmapped raw project name in the shared ``.pending`` file.

    Deduplicated: if ``raw_name`` is already listed, only the header
    (source, discovered) is refreshed.  Returns True if a new entry was
    added, False if it was already present.
    """
    path = pending_path(repo_base)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _parse_raw_names(path.read_text()) if path.is_file() else []
    added = raw_name not in existing
    if added:
        existing.append(raw_name)

    _rewrite(path, existing)
    return added


def prune_pending(repo_base: Path, mapping: dict[str, str]) -> list[str]:
    """Drop snippets for raw names that now appear in ``mapping``.

    Deletes the file entirely if no snippets remain.  Returns the list
    of raw names that were removed.
    """
    path = pending_path(repo_base)
    if not path.is_file():
        return []

    existing = _parse_raw_names(path.read_text())
    remaining = [r for r in existing if r not in mapping]
    removed = [r for r in existing if r in mapping]

    if not remaining:
        path.unlink()
        return removed
    if removed:
        _rewrite(path, remaining)
    return removed
