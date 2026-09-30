"""Merge two differing Markdown files by keeping both versions in one file."""

import logging
import socket
from datetime import datetime, timezone
from pathlib import Path

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


def merge_conflicting_file(local: Path, repo: Path) -> None:
    """Merge two differing files by keeping both versions with conflict markers."""
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).isoformat()

    local_body, local_frontmatter = split_frontmatter(local.read_text())
    repo_body, repo_frontmatter = split_frontmatter(repo.read_text())

    merged_frontmatter = {**repo_frontmatter, **local_frontmatter, "conflict": "true"}
    frontmatter_lines = "\n".join(f"{k}: {v}" for k, v in merged_frontmatter.items())

    merged = (
        f"---\n{frontmatter_lines}\n---\n\n"
        f"<!-- CONFLICT ({now}): local ({hostname}) version -->\n"
        f"{local_body.strip()}\n\n"
        f"<!-- CONFLICT ({now}): repo version -->\n"
        f"{repo_body.strip()}\n"
    )

    repo.write_text(merged)
    local.write_text(merged)
    log.info("Conflict-merged %s (both versions preserved)", repo.name)
