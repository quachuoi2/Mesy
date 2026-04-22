"""Git operations for the Mesy repo."""

import logging
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_DIR

log = logging.getLogger("mesy")


def git(*args: str, data_dir: Path, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(data_dir)] + list(args)
    log.debug("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check)


def has_local_changes(data_dir: Path) -> bool:
    result = git("status", "--porcelain", data_dir=data_dir)
    return bool(result.stdout.strip())


def commit_and_push(stage_dirs: list[str], data_dir: Path) -> None:
    """Stage changes, commit, rebase-pull, push.

    Args:
        stage_dirs: directories to git-add.
        data_dir: root of the git repo to operate on.
    """
    if not has_local_changes(data_dir):
        return

    git("add", *stage_dirs, data_dir=data_dir)

    result = git("diff", "--cached", "--quiet", data_dir=data_dir)
    if result.returncode == 0:
        return

    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # List changed files for the commit message
    name_status = git("diff", "--cached", "--name-status", data_dir=data_dir)
    changed_lines = []
    for line in name_status.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, filepath = parts
            prefix = {"A": "add", "M": "update", "D": "delete"}.get(status, status)
            changed_lines.append(f"  {prefix} {filepath}")

    msg = f"sync from {hostname} at {now}"
    if changed_lines:
        msg += "\n\n" + "\n".join(changed_lines)
    commit_result = git("commit", "-m", msg, data_dir=data_dir)
    if commit_result.returncode != 0:
        log.warning("commit failed: %s", commit_result.stderr)
        return

    log.info("Committed: %s", msg)

    pull_result = git("pull", "--rebase", "origin", "main", data_dir=data_dir)
    if pull_result.returncode != 0:
        log.warning("pull --rebase failed, attempting conflict resolution")
        resolve_conflicts(data_dir)

    push_result = git("push", "origin", "main", data_dir=data_dir)
    if push_result.returncode != 0:
        log.warning("push failed (offline?): %s", push_result.stderr)


def resolve_conflicts(data_dir: Path) -> None:
    """Handle rebase conflicts by keeping both versions."""
    result = git("diff", "--name-only", "--diff-filter=U", data_dir=data_dir)
    if not result.stdout.strip():
        cont = git("rebase", "--continue", data_dir=data_dir)
        if cont.returncode != 0:
            git("rebase", "--abort", data_dir=data_dir)
        return

    conflicted = result.stdout.strip().split("\n")
    for filepath in conflicted:
        full_path = data_dir / filepath
        if not full_path.exists():
            continue

        content = full_path.read_text()
        if "<<<<<<<" not in content:
            continue

        hostname = socket.gethostname()
        now = datetime.now(timezone.utc).isoformat()
        lines = content.split("\n")
        resolved = []
        in_conflict = False
        section = "ours"

        for line in lines:
            if line.startswith("<<<<<<<"):
                in_conflict = True
                section = "ours"
                resolved.append(f"<!-- CONFLICT ({now}): local ({hostname}) version -->")
            elif line.startswith("=======") and in_conflict:
                section = "theirs"
                resolved.append(f"\n<!-- CONFLICT ({now}): remote version -->")
            elif line.startswith(">>>>>>>") and in_conflict:
                in_conflict = False
            else:
                resolved.append(line)

        resolved_text = "\n".join(resolved)
        if resolved_text.startswith("---\n"):
            parts = resolved_text.split("---\n", 2)
            if len(parts) >= 3:
                resolved_text = f"---\nconflict: true\n{parts[1]}---\n{parts[2]}"

        full_path.write_text(resolved_text)
        git("add", filepath, data_dir=data_dir)

    git("rebase", "--continue", data_dir=data_dir)


def pull_remote(data_dir: Path) -> None:
    """Pull remote changes (fast-forward or rebase)."""
    result = git("pull", "--rebase", "origin", "main", data_dir=data_dir)
    if result.returncode == 0 and "Already up to date" not in result.stdout:
        log.info("Pulled remote changes")
    elif result.returncode != 0:
        log.debug("pull failed (offline?): %s", result.stderr)


def split_frontmatter(text: str) -> tuple[str, dict]:
    """Split markdown into (body, frontmatter_dict)."""
    if not text.startswith("---\n"):
        return text, {}
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return text, {}
    fm = {}
    for line in parts[1].strip().split("\n"):
        if ": " in line:
            key, val = line.split(": ", 1)
            fm[key.strip()] = val.strip()
    return parts[2], fm


def merge_conflicting_file(local: Path, repo: Path) -> None:
    """Merge two differing files by keeping both versions with conflict markers."""
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).isoformat()

    local_body, local_fm = split_frontmatter(local.read_text())
    repo_body, repo_fm = split_frontmatter(repo.read_text())

    merged_fm = {**repo_fm, **local_fm, "conflict": "true"}
    fm_lines = "\n".join(f"{k}: {v}" for k, v in merged_fm.items())

    merged = (
        f"---\n{fm_lines}\n---\n\n"
        f"<!-- CONFLICT ({now}): local ({hostname}) version -->\n"
        f"{local_body.strip()}\n\n"
        f"<!-- CONFLICT ({now}): repo version -->\n"
        f"{repo_body.strip()}\n"
    )

    repo.write_text(merged)
    local.write_text(merged)
    log.info("Conflict-merged %s (both versions preserved)", repo.name)
