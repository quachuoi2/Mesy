"""Git operations for the Mesy repo."""

import logging
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mesy")


def git(*args: str, repo_base: Path, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(repo_base)] + list(args)
    log.debug("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check)


def has_local_changes(repo_base: Path) -> bool:
    result = git("status", "--porcelain", repo_base=repo_base)
    return bool(result.stdout.strip())


def current_branch(repo_base: Path) -> str:
    result = git("symbolic-ref", "--short", "HEAD", repo_base=repo_base)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "main"


def commit_and_push(stage_dirs: list[str], repo_base: Path) -> None:
    """Stage changes, commit, rebase-pull, push.

    Args:
        stage_dirs: directories to git-add.
        repo_base: root of the git repo to operate on.
    """
    if not _commit(stage_dirs, repo_base):
        return

    pull_remote(repo_base)

    push_result = git("push", "origin", current_branch(repo_base), repo_base=repo_base)
    if push_result.returncode != 0:
        log.warning("push failed (offline?): %s", push_result.stderr)


def _commit(stage_dirs: list[str], repo_base: Path) -> bool:
    """True when a new commit was made."""
    if not has_local_changes(repo_base):
        return False

    git("add", *stage_dirs, repo_base=repo_base)

    result = git("diff", "--cached", "--quiet", repo_base=repo_base)
    if result.returncode == 0:
        return False

    msg = _commit_message(repo_base)
    commit_result = git("commit", "-m", msg, repo_base=repo_base)
    if commit_result.returncode != 0:
        log.warning("commit failed: %s", commit_result.stderr)
        return False

    log.info("Committed: %s", msg)
    return True


def _commit_message(repo_base: Path) -> str:
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    name_status = git("diff", "--cached", "--name-status", repo_base=repo_base)
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


def resolve_conflicts(repo_base: Path) -> None:
    """Handle rebase conflicts by keeping both versions."""
    result = git("diff", "--name-only", "--diff-filter=U", repo_base=repo_base)
    if not result.stdout.strip():
        continue_result = git("rebase", "--continue", repo_base=repo_base)
        if continue_result.returncode != 0:
            git("rebase", "--abort", repo_base=repo_base)
        return

    for filepath in result.stdout.strip().split("\n"):
        _resolve_file(repo_base, filepath)

    git("rebase", "--continue", repo_base=repo_base)


def _resolve_file(repo_base: Path, filepath: str) -> None:
    full_path = repo_base / filepath
    if not full_path.exists():
        return

    content = full_path.read_text()
    if "<<<<<<<" not in content:
        return

    full_path.write_text(_flag_conflict(_replace_markers(content)))
    git("add", filepath, repo_base=repo_base)


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


def pull_remote(repo_base: Path) -> None:
    """Pull remote changes with rebase; resolve conflicts if the rebase stops."""
    result = git("pull", "--rebase", "origin", current_branch(repo_base), repo_base=repo_base)
    if result.returncode == 0:
        if "Already up to date" not in result.stdout:
            log.info("Pulled remote changes")
        return

    log.debug("pull failed (offline?), attempting conflict resolution: %s", result.stderr)
    resolve_conflicts(repo_base)