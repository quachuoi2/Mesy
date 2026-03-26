#!/usr/bin/env python3
"""Mesy — Claude Code memory sync setup (cross-platform).

Run from inside the cloned Mesy repo on any new system:
    python setup.py
"""

import filecmp
import os
import platform
import shutil
import socket
import subprocess
import sys
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
IS_WINDOWS = platform.system() == "Windows"
PROJECTS_CONF_PATH = REPO_DIR / "projects.conf"

if IS_WINDOWS:
    CLAUDE_DIR = Path(os.environ["USERPROFILE"]) / ".claude"
else:
    CLAUDE_DIR = Path.home() / ".claude"


def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


def merge_conflicting_file(local: Path, repo: Path) -> None:
    """Merge two differing files by keeping both versions with conflict markers."""
    hostname = socket.gethostname()
    now = datetime.now(timezone.utc).isoformat()

    local_content = local.read_text()
    repo_content = repo.read_text()

    # Strip existing frontmatter from both to merge cleanly
    local_body, local_fm = _split_frontmatter(local_content)
    repo_body, repo_fm = _split_frontmatter(repo_content)

    # Merge frontmatter keys (repo as base, local overrides, add conflict flag)
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
    log("conflict", f"  {repo.name}: both versions preserved (conflict: true)")


def _split_frontmatter(text: str) -> tuple[str, dict]:
    """Split markdown into (body, frontmatter_dict). Returns ({}, body) if no frontmatter."""
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


def make_symlink(link: Path, target: Path) -> None:
    """Create a symlink, with Windows-compatible directory symlink support."""
    if link.is_symlink():
        log("ok", f"Symlink already exists: {link}")
        return

    if link.is_dir():
        # Migrate existing memory into repo
        log("migrate", f"Moving existing memory from {link}")
        target.mkdir(parents=True, exist_ok=True)
        for f in link.iterdir():
            if f.is_dir():
                continue  # skip subdirectories within memory
            dest = target / f.name
            if not dest.exists():
                # New file — just copy it
                shutil.copy2(f, dest)
                log("migrate", f"  copied {f.name}")
            elif not filecmp.cmp(f, dest, shallow=False):
                # Both exist with different content — merge with conflict markers
                merge_conflicting_file(local=f, repo=dest)
            else:
                log("ok", f"  {f.name} identical, skipping")
        shutil.rmtree(link)

    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        # os.symlink works on Windows with developer mode enabled or admin
        # Fall back to junction if symlink fails
        try:
            os.symlink(target, link, target_is_directory=True)
            log("ok", f"Symlinked {link.name}")
        except OSError:
            log("warn", "Symlink failed (enable Developer Mode in Windows Settings)")
            log("info", "Trying directory junction instead...")
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
            )
            log("ok", f"Junction created for {link.name}")
    else:
        os.symlink(target, link)
        log("ok", f"Symlinked {link.name}")


def load_project_mappings() -> dict[str, str]:
    """Load projects.conf and return {raw_claude_name: canonical_name} mapping."""
    mapping = {}
    if not PROJECTS_CONF_PATH.exists():
        return mapping
    cfg = ConfigParser()
    cfg.read(PROJECTS_CONF_PATH)
    for section in cfg.sections():
        canonical = section
        paths_str = cfg.get(section, "paths", fallback="")
        for raw_name in paths_str.split(","):
            raw_name = raw_name.strip()
            if raw_name:
                mapping[raw_name] = canonical
    return mapping


def migrate_repo_to_canonical(mapping: dict[str, str]) -> None:
    """Rename raw-named project dirs in the repo to their canonical names."""
    projects_dir = REPO_DIR / "projects"
    if not projects_dir.exists():
        return

    # Build reverse: canonical -> list of raw names
    canonical_raws: dict[str, list[str]] = {}
    for raw, canonical in mapping.items():
        canonical_raws.setdefault(canonical, []).append(raw)

    for canonical, raw_names in canonical_raws.items():
        canonical_dir = projects_dir / canonical
        if canonical_dir.exists():
            continue  # already migrated

        # Check if any raw-named directory exists in repo
        for raw_name in raw_names:
            raw_dir = projects_dir / raw_name
            if raw_dir.exists() and raw_dir.is_dir():
                raw_dir.rename(canonical_dir)
                log("migrate", f"Renamed repo dir: {raw_name} -> {canonical}")
                break


def setup_symlinks() -> None:
    """Symlink global and project memories."""
    # Global memory
    make_symlink(CLAUDE_DIR / "memory", REPO_DIR / "global")

    mapping = load_project_mappings()

    # Rename any raw-named dirs in repo to canonical names
    migrate_repo_to_canonical(mapping)

    # Symlink mapped projects: Claude's raw name -> canonical repo dir
    claude_projects_dir = CLAUDE_DIR / "projects"
    if claude_projects_dir.exists():
        for project_dir in claude_projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            raw_name = project_dir.name
            memory_path = project_dir / "memory"

            if memory_path.is_symlink():
                continue  # already set up

            if raw_name in mapping:
                canonical = mapping[raw_name]
                canonical_dir = REPO_DIR / "projects" / canonical
                make_symlink(memory_path, canonical_dir)
            elif memory_path.is_dir():
                # Unmapped — stage in .pending
                pending_dir = REPO_DIR / "projects" / ".pending" / raw_name
                pending_dir.mkdir(parents=True, exist_ok=True)
                make_symlink(memory_path, pending_dir)
                # Write marker
                marker = pending_dir / ".needs_mapping"
                if not marker.exists():
                    hostname = socket.gethostname()
                    now = datetime.now(timezone.utc).isoformat()
                    marker.write_text(
                        f"source: {hostname}\n"
                        f"discovered: {now}\n"
                        f"raw_name: {raw_name}\n"
                        f"\n"
                        f"Add a mapping in projects.conf:\n"
                        f"  [YourProjectName]\n"
                        f"  paths = {raw_name}\n"
                    )
                log("pending", f"{raw_name} has no mapping — staged in .pending/")
                log("info", f"  Add to projects.conf: [YourProjectName] / paths = {raw_name}")

    # Also symlink for mapped raw names that exist in projects.conf
    # but whose Claude project dirs may not exist yet on this system
    # (they'll be created when Claude is used in that directory)
    for raw_name, canonical in mapping.items():
        claude_project_dir = claude_projects_dir / raw_name
        memory_path = claude_project_dir / "memory"
        if memory_path.exists():
            continue  # already handled above
        canonical_dir = REPO_DIR / "projects" / canonical
        if canonical_dir.exists():
            claude_project_dir.mkdir(parents=True, exist_ok=True)
            make_symlink(memory_path, canonical_dir)
            log("ok", f"Pre-linked {raw_name} -> {canonical}")


def install_systemd_service() -> None:
    """Install and enable the systemd user service (Linux only)."""
    service_template = REPO_DIR / "claude-memory-sync.service"
    if not service_template.exists():
        log("skip", "Service template not found")
        return

    user_service_dir = Path.home() / ".config" / "systemd" / "user"
    user_service_dir.mkdir(parents=True, exist_ok=True)

    service_content = service_template.read_text().replace("%REPO_DIR%", str(REPO_DIR))
    dest = user_service_dir / "claude-memory-sync.service"
    dest.write_text(service_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "claude-memory-sync"], check=False
    )
    log("ok", "Systemd service installed and started")

    # Enable lingering so service runs after logout
    result = subprocess.run(
        ["loginctl", "enable-linger", os.environ.get("USER", "")],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        log("warn", "Could not enable lingering (may need sudo)")


def install_task_scheduler(python_exe: str) -> None:
    """Install a Windows Task Scheduler task for the sync daemon."""
    task_name = "MesyClaudeMemorySync"
    sync_script = REPO_DIR / "sync.py"

    # Delete existing task if present
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        check=False,
    )

    # Create task that runs at logon and restarts on failure
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/TR",
            f'"{python_exe}" "{sync_script}"',
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        log("ok", "Task Scheduler task created (runs at logon)")
        # Start the task now
        subprocess.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            check=False,
        )
        log("ok", "Sync daemon started")
    else:
        log("warn", f"Task Scheduler setup failed: {result.stderr.strip()}")
        log("info", f"You can run manually: {python_exe} {sync_script}")


def install_daemon() -> None:
    """Install the appropriate background daemon for this platform."""
    if IS_WINDOWS:
        python_exe = sys.executable
        install_task_scheduler(python_exe)
    else:
        install_systemd_service()


def git_push_migrated() -> None:
    """Commit and push any migrated files."""
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "-A"], check=False)
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"],
        check=False,
    )
    if result.returncode != 0:
        import socket

        hostname = socket.gethostname()
        subprocess.run(
            [
                "git",
                "-C",
                str(REPO_DIR),
                "commit",
                "-m",
                f"onboard {hostname}: migrate existing memory",
            ],
            check=False,
        )
        push = subprocess.run(
            ["git", "-C", str(REPO_DIR), "push", "origin", "main"],
            capture_output=True,
            check=False,
        )
        if push.returncode != 0:
            log("warn", "Push failed (offline?), will sync later")


def main() -> None:
    print("=== Mesy: Claude Memory Sync Setup ===")
    print(f"Platform: {platform.system()}")
    print(f"Repo:     {REPO_DIR}")
    print(f"Claude:   {CLAUDE_DIR}")
    print()

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    setup_symlinks()
    install_daemon()
    git_push_migrated()

    print()
    print("=== Setup complete ===")
    print("Memory sync daemon is running. Changes will auto-sync to GitHub.")
    if IS_WINDOWS:
        print("Check status: schtasks /Query /TN MesyClaudeMemorySync")
    else:
        print("Check status: systemctl --user status claude-memory-sync")


if __name__ == "__main__":
    main()
