"""Git operations for the Mesy repo."""

import logging
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mesy")


def git(*args: str, data_dir: Path, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(data_dir)] + list(args)
    log.debug("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check)


def has_local_changes(data_dir: Path) -> bool:
    result = git("status", "--porcelain", data_dir=data_dir)
    return bool(result.stdout.strip())


def current_branch(data_dir: Path) -> str:
    result = git("symbolic-ref", "--short", "HEAD", data_dir=data_dir)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "main"


def commit_and_push(stage_dirs: list[str], data_dir: Path) -> None:
    """Stage changes, commit, rebase-pull, push.

    Args:
        stage_dirs: directories to git-add.
        data_dir: root of the git repo to operate on.
    """
    if not _commit(stage_dirs, data_dir):
        return

    branch = current_branch(data_dir)
    pull_result = git("pull", "--rebase", "origin", branch, data_dir=data_dir)
    if pull_result.returncode != 0:
        log.warning("pull --rebase failed, attempting conflict resolution")
        resolve_conflicts(data_dir)

    push_result = git("push", "origin", branch, data_dir=data_dir)
    if push_result.returncode != 0:
        log.warning("push failed (offline?): %s", push_result.stderr)


def _commit(stage_dirs: list[str], data_dir: Path) -> bool:
    """True when a new commit was made."""
    if not has_local_changes(data_dir):
        return False

    git("add", *stage_dirs, data_dir=data_dir)

    result = git("diff", "--cached", "--quiet", data_dir=data_dir)
    if result.returncode == 0:
        return False

    msg = _commit_message(data_dir)
    commit_result = git("commit", "-m", msg, data_dir=data_dir)
    if commit_result.returncode != 0:
        log.warning("commit failed: %s", commit_result.stderr)
        return False

    log.info("Committed: %s", msg)
    return True


def _commit_message(data_dir: Path) -> str:
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
    return msg


def resolve_conflicts(data_dir: Path) -> None:
    """Handle rebase conflicts by keeping both versions."""
    result = git("diff", "--name-only", "--diff-filter=U", data_dir=data_dir)
    if not result.stdout.strip():
        cont = git("rebase", "--continue", data_dir=data_dir)
        if cont.returncode != 0:
            git("rebase", "--abort", data_dir=data_dir)
        return

    for filepath in result.stdout.strip().split("\n"):
        _resolve_file(data_dir, filepath)

    git("rebase", "--continue", data_dir=data_dir)


def _resolve_file(data_dir: Path, filepath: str) -> None:
    full_path = data_dir / filepath
    if not full_path.exists():
        return

    content = full_path.read_text()
    if "<<<<<<<" not in content:
        return

    full_path.write_text(_flag_conflict(_replace_markers(content)))
    git("add", filepath, data_dir=data_dir)


def _replace_markers(content: str) -> str:
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).isoformat()
    resolved = []
    in_conflict = False

    for line in content.split("\n"):
        if line.startswith("<<<<<<<"):
            in_conflict = True
            resolved.append(f"<!-- CONFLICT ({now}): local ({hostname}) version -->")
        elif line.startswith("=======") and in_conflict:
            resolved.append(f"\n<!-- CONFLICT ({now}): remote version -->")
        elif line.startswith(">>>>>>>") and in_conflict:
            in_conflict = False
        else:
            resolved.append(line)

    return "\n".join(resolved)


def _flag_conflict(text: str) -> str:
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            return f"---\nconflict: true\n{parts[1]}---\n{parts[2]}"
    return text


def pull_remote(data_dir: Path) -> None:
    """Pull remote changes (fast-forward or rebase)."""
    result = git("pull", "--rebase", "origin", current_branch(data_dir), data_dir=data_dir)
    if result.returncode == 0 and "Already up to date" not in result.stdout:
        log.info("Pulled remote changes")
    elif result.returncode != 0:
        log.debug("pull failed (offline?): %s", result.stderr)