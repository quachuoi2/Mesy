#!/usr/bin/env python3
"""Mesy — Claude Code memory sync daemon.

Watches ~/Workspace/Mesy/ for .md changes and syncs via git to a GitHub remote.
Also detects new Claude Code project memory directories and symlinks them into the repo.
"""

import filecmp
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
CONFIG_PATH = REPO_DIR / "sync.conf"

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("mesy")


def load_config() -> dict:
    defaults = {
        "debounce_seconds": "5",
        "pull_interval_seconds": "60",
        "scan_interval_seconds": "60",
    }
    cfg = ConfigParser()
    cfg.read_dict({"mesy": defaults})
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    return dict(cfg["mesy"])


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(REPO_DIR)] + list(args)
    log.debug("git %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check)


def has_local_changes() -> bool:
    result = git("status", "--porcelain")
    return bool(result.stdout.strip())


def commit_and_push() -> None:
    """Stage .md changes, commit, rebase-pull, push."""
    if not has_local_changes():
        return

    git("add", "-A")

    # Check if there's anything staged after add
    result = git("diff", "--cached", "--quiet")
    if result.returncode == 0:
        return

    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"sync from {hostname} at {now}"
    commit_result = git("commit", "-m", msg)
    if commit_result.returncode != 0:
        log.warning("commit failed: %s", commit_result.stderr)
        return

    log.info("Committed: %s", msg)

    # Rebase-pull then push
    pull_result = git("pull", "--rebase", "origin", "main")
    if pull_result.returncode != 0:
        log.warning("pull --rebase failed, attempting conflict resolution")
        resolve_conflicts()

    push_result = git("push", "origin", "main")
    if push_result.returncode != 0:
        log.warning("push failed (offline?): %s", push_result.stderr)


def resolve_conflicts() -> None:
    """Handle rebase conflicts by keeping both versions."""
    # Find conflicted files
    result = git("diff", "--name-only", "--diff-filter=U")
    if not result.stdout.strip():
        # No conflicts, rebase may have just failed for another reason
        git("rebase", "--abort")
        return

    conflicted = result.stdout.strip().split("\n")
    for filepath in conflicted:
        full_path = REPO_DIR / filepath
        if not full_path.exists():
            continue

        content = full_path.read_text()
        if "<<<<<<<" not in content:
            continue

        # Replace conflict markers with labeled sections
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

        # Inject conflict: true into frontmatter if present
        resolved_text = "\n".join(resolved)
        if resolved_text.startswith("---\n"):
            parts = resolved_text.split("---\n", 2)
            if len(parts) >= 3:
                resolved_text = f"---\nconflict: true\n{parts[1]}---\n{parts[2]}"

        full_path.write_text(resolved_text)
        git("add", filepath)

    git("rebase", "--continue")


def pull_remote() -> None:
    """Pull remote changes (fast-forward or rebase)."""
    result = git("pull", "--rebase", "origin", "main")
    if result.returncode == 0 and "Already up to date" not in result.stdout:
        log.info("Pulled remote changes")
    elif result.returncode != 0:
        log.debug("pull failed (offline?): %s", result.stderr)


def _split_frontmatter(text: str) -> tuple[str, dict]:
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


def _merge_conflicting_file(local: Path, repo: Path) -> None:
    """Merge two differing files by keeping both versions with conflict markers."""
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).isoformat()

    local_body, local_fm = _split_frontmatter(local.read_text())
    repo_body, repo_fm = _split_frontmatter(repo.read_text())

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
    log.info("Conflict-merged %s (both versions preserved)", repo.name)


def scan_new_projects() -> None:
    """Find Claude Code project memory dirs that aren't symlinked and adopt them."""
    if not PROJECTS_DIR.exists():
        return

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        memory_dir = project_dir / "memory"
        if not memory_dir.exists():
            continue
        if memory_dir.is_symlink():
            continue

        # Real directory — adopt it into the repo
        project_name = project_dir.name
        repo_project_dir = REPO_DIR / "projects" / project_name

        if repo_project_dir.exists():
            # Merge: copy new files, conflict-merge differing files
            for f in memory_dir.iterdir():
                if f.is_dir():
                    continue
                dest = repo_project_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                elif not filecmp.cmp(f, dest, shallow=False):
                    _merge_conflicting_file(local=f, repo=dest)
        else:
            # Move entire directory into repo
            shutil.copytree(memory_dir, repo_project_dir)

        # Replace with symlink
        shutil.rmtree(memory_dir)
        memory_dir.symlink_to(repo_project_dir)
        log.info("Adopted new project memory: %s", project_name)


def get_md_mtimes() -> dict[str, float]:
    """Get modification times of all .md files in repo."""
    mtimes = {}
    for md_file in REPO_DIR.rglob("*.md"):
        # Skip .git directory
        if ".git" in md_file.parts:
            continue
        try:
            mtimes[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return mtimes


def run_with_watchdog(config: dict) -> None:
    """Use watchdog for file-change detection."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    debounce = float(config["debounce_seconds"])
    pull_interval = float(config["pull_interval_seconds"])
    scan_interval = float(config["scan_interval_seconds"])

    class MdChangeHandler(FileSystemEventHandler):
        def __init__(self):
            self.last_change = 0.0

        def on_any_event(self, event):
            if event.is_directory:
                return
            if event.src_path.endswith(".md") and ".git" not in event.src_path:
                self.last_change = time.time()

    handler = MdChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(REPO_DIR), recursive=True)
    observer.start()
    log.info("Watching with watchdog (debounce=%ss)", debounce)

    last_pull = time.time()
    last_scan = time.time()

    try:
        while True:
            time.sleep(1)
            now = time.time()

            # Debounced commit+push on local changes
            if handler.last_change and (now - handler.last_change) > debounce:
                handler.last_change = 0.0
                commit_and_push()

            # Periodic pull
            if (now - last_pull) > pull_interval:
                last_pull = now
                pull_remote()

            # Periodic new-project scan
            if (now - last_scan) > scan_interval:
                last_scan = now
                scan_new_projects()

    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def run_with_polling(config: dict) -> None:
    """Fallback: poll for changes using mtime comparison."""
    poll_interval = max(float(config["debounce_seconds"]), 10)
    pull_interval = float(config["pull_interval_seconds"])
    scan_interval = float(config["scan_interval_seconds"])

    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes = get_md_mtimes()
    last_pull = time.time()
    last_scan = time.time()

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()

            # Check for local changes
            current_mtimes = get_md_mtimes()
            if current_mtimes != prev_mtimes:
                prev_mtimes = current_mtimes
                commit_and_push()

            # Periodic pull
            if (now - last_pull) > pull_interval:
                last_pull = now
                pull_remote()

            # Periodic new-project scan
            if (now - last_scan) > scan_interval:
                last_scan = now
                scan_new_projects()

    except KeyboardInterrupt:
        pass


def main() -> None:
    config = load_config()
    log.info("Mesy sync daemon starting (repo: %s)", REPO_DIR)

    # Initial sync
    pull_remote()
    scan_new_projects()
    commit_and_push()

    # Choose watch method
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(config)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(config)


if __name__ == "__main__":
    main()
