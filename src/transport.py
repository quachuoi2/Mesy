"""Git transport that resolves merges away from files exposed through symlinks.

Codex repositories use this transport. Legacy targets can opt into it simply by
sharing a repository with a Codex target. Unrelated staged work is never committed.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.links import atomic_write, checked_destination

log = logging.getLogger("mesy")


def run_git(data_dir: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True,
                          text=not binary, timeout=30)


def _require(result: subprocess.CompletedProcess) -> str:
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git operation failed")
    return result.stdout.strip()


def _conflicts(data_dir: Path, temporary: Path, local: str, remote: str) -> None:
    output = _require(run_git(temporary, "diff", "--name-only", "--diff-filter=U", "-z"))
    directory = checked_destination(data_dir, data_dir / ".mesy-local" / "git-conflicts" / f"{local}-{remote}")
    for name in output.split("\0"):
        if not name:
            continue
        for stage, label in ((1, "base"), (2, "local"), (3, "remote")):
            result = run_git(temporary, "show", f":{stage}:{name}", binary=True)
            if result.returncode == 0:
                path = checked_destination(directory, directory / label / name)
                atomic_write(path, result.stdout)
    log.error("Git conflict; live files unchanged. Versions saved in %s", directory)


def sync_repository(data_dir: Path, paths: list[str], *, remote: str = "origin",
                    branch: str | None = None, publish_paths: list[str] | None = None) -> bool:
    """Commit managed paths, merge in a detached worktree, fast-forward, and push.

    Unrelated dirty or staged files defer the merge but remain untouched. A failed
    push is retried on every call even with a clean working tree. Git rejects races
    at publication; failed merges never enter the live worktree.
    """
    if not paths:
        return True
    try:
        root = data_dir.resolve()
        safe_paths = []
        for relative in paths:
            candidate = checked_destination(root, root / relative)
            if candidate == root or ".git" in Path(relative).parts or ".mesy-local" in Path(relative).parts:
                raise ValueError(f"Unsafe staging path: {relative}")
            # Check before staging, including files created through a linked folder.
            entries = [candidate, *candidate.rglob("*")] if candidate.is_dir() else [candidate]
            if any(entry.is_symlink() for entry in entries):
                raise ValueError(f"Nested symlink in managed content: {relative}")
            tracked = _require(run_git(root, "ls-files", "--", relative))
            if candidate.exists() or tracked:
                safe_paths.append(relative)
        current_branch = _require(run_git(root, "symbolic-ref", "--short", "HEAD"))
        if not current_branch:
            raise ValueError("A checked-out branch is required")
        branch = branch or current_branch
        _require(run_git(root, "check-ref-format", "--branch", branch))
        if safe_paths:
            status = _require(run_git(root, "status", "--porcelain", "--", *safe_paths))
            if status:
                _require(run_git(root, "add", "--", *safe_paths))
                diff = run_git(root, "diff", "--cached", "--quiet", "--", *safe_paths)
                if diff.returncode == 1:
                    message = f"sync from {socket.gethostname()} at {datetime.now(timezone.utc).isoformat()}"
                    _require(run_git(root, "commit", "--only", "-m", message, "--", *safe_paths))
                elif diff.returncode != 0:
                    _require(diff)
        # Local-only metadata must not be committed. Ignore it for dirty checks.
        dirty = _require(run_git(root, "status", "--porcelain", "--untracked-files=no"))
        if dirty:
            log.warning("Deferring sync of %s: unrelated staged/modified files remain", root)
            return False
        fetched = run_git(root, "fetch", remote, branch)
        if fetched.returncode:
            # A new, empty remote has no branch to fetch. Distinguish that from
            # network/auth failures; never overwrite an existing remote branch.
            advertised = run_git(root, "ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}")
            if advertised.returncode == 2:
                _require(run_git(root, "push", remote, f"HEAD:refs/heads/{branch}"))
                return True
            _require(fetched)
        local = _require(run_git(root, "rev-parse", "HEAD"))
        incoming = _require(run_git(root, "rev-parse", "FETCH_HEAD"))
        ancestor = run_git(root, "merge-base", "--is-ancestor", incoming, local)
        if ancestor.returncode not in (0, 1):
            _require(ancestor)
        if ancestor.returncode == 1:
            with tempfile.TemporaryDirectory(prefix="mesy-merge-") as temporary_root:
                temporary = Path(temporary_root) / "worktree"
                _require(run_git(root, "worktree", "add", "--detach", str(temporary), local))
                try:
                    merged = run_git(temporary, "-c", "commit.gpgsign=false", "merge", "--no-edit", incoming)
                    if merged.returncode:
                        _conflicts(root, temporary, local, incoming)
                        return False
                    result = _require(run_git(temporary, "rev-parse", "HEAD"))
                    local_metadata = _require(run_git(temporary, "ls-files", "--", ".mesy-local"))
                    if local_metadata:
                        raise ValueError("Remote tracks .mesy-local; refusing to overwrite local recovery state")
                    # Do not publish symlinks or disallowed content received remotely.
                    for relative in publish_paths or paths:
                        destination = checked_destination(temporary, temporary / relative)
                        entries = [destination, *destination.rglob("*")] if destination.is_dir() else [destination]
                        if any(entry.is_symlink() for entry in entries):
                            raise ValueError(f"Remote managed content contains a symlink: {relative}")
                    if _require(run_git(root, "rev-parse", "HEAD")) != local:
                        log.warning("Local HEAD changed during sync; retrying next cycle")
                        return False
                    # Git checks tracked modifications and untracked collisions itself.
                    _require(run_git(root, "merge", "--ff-only", result))
                finally:
                    removed = run_git(root, "worktree", "remove", "--force", str(temporary))
                    if removed.returncode:
                        log.warning("Temporary worktree cleanup failed: %s", removed.stderr.strip())
        _require(run_git(root, "push", remote, f"HEAD:refs/heads/{branch}"))
        return True
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        log.warning("Sync deferred for %s: %s", data_dir, exc)
        return False
