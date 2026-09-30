"""Conflict format: keep both versions of a file, with markers and a ``conflict: true`` flag."""

import logging
import socket
from pathlib import Path

from src.timestamp import now_iso

log = logging.getLogger("mesy")


def split_frontmatter(text: str) -> tuple[str, dict]:
    """Split markdown into (body, frontmatter_dict)."""
    if not text.startswith("---\n"):
        return text, {}

    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return text, {}

    frontmatter = {}
    for line in parts[1].strip().split("\n"):
        if ": " in line:
            key, val = line.split(": ", 1)
            frontmatter[key.strip()] = val.strip()

    return parts[2], frontmatter


def conflict_marker(now: str, side: str) -> str:
    return f"<!-- CONFLICT ({now}): {side} version -->"


def flag_conflict(text: str) -> str:
    """Add ``conflict: true`` to the frontmatter, creating the frontmatter when there is none."""
    parts = text.split("---\n", 2)
    if text.startswith("---\n") and len(parts) == 3:
        return f"---\nconflict: true\n{parts[1]}---\n{parts[2]}"

    return f"---\nconflict: true\n---\n\n{text}"


def merge_conflicting_file(local: Path, repo: Path) -> None:
    """Merge two differing files by keeping both versions with conflict markers."""
    hostname = socket.gethostname()
    now = now_iso()

    local_body, local_frontmatter = split_frontmatter(local.read_text())
    repo_body, repo_frontmatter = split_frontmatter(repo.read_text())

    merged_frontmatter = {**repo_frontmatter, **local_frontmatter}
    frontmatter_lines = "".join(f"{k}: {v}\n" for k, v in merged_frontmatter.items() if k != "conflict")

    merged = flag_conflict(
        f"---\n{frontmatter_lines}---\n\n"
        f"{conflict_marker(now, f'local ({hostname})')}\n"
        f"{local_body.strip()}\n\n"
        f"{conflict_marker(now, 'repo')}\n"
        f"{repo_body.strip()}\n"
    )

    repo.write_text(merged)
    local.write_text(merged)
    log.info("Conflict-merged %s (both versions preserved)", repo.name)
