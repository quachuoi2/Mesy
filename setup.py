#!/usr/bin/env python3
"""Mesy — Multi-target memory sync setup (cross-platform).

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

    local_body, local_fm = _split_frontmatter(local_content)
    repo_body, repo_fm = _split_frontmatter(repo_content)

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


def make_file_symlink(link: Path, target: Path) -> None:
    """Create a symlink for a single file, migrating existing content if needed."""
    if link.is_symlink():
        if link.resolve() == target.resolve():
            log("ok", f"Symlink already exists: {link.name}")
            return
        log("relink", f"Updating symlink: {link.name} -> {target}")
        link.unlink()

    if link.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(link, target)
            log("migrate", f"Copied {link.name} into repo")
        elif not filecmp.cmp(link, target, shallow=False):
            merge_conflicting_file(local=link, repo=target)
        else:
            log("ok", f"{link.name} identical, skipping")
        link.unlink()
    elif not target.exists():
        return

    link.parent.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        try:
            os.symlink(target, link)
            log("ok", f"Symlinked {link.name}")
        except OSError:
            shutil.copy2(target, link)
            log("warn", f"Symlink failed for {link.name}, copied instead (edits won't sync back!)")
    else:
        os.symlink(target, link)
        log("ok", f"Symlinked {link.name}")


def make_symlink(link: Path, target: Path) -> None:
    """Create a symlink for a directory, with Windows-compatible support."""
    if link.is_symlink():
        if link.resolve() == target.resolve():
            log("ok", f"Symlink already exists: {link}")
            return
        log("relink", f"Updating symlink: {link} -> {target}")
        link.unlink()

    if link.is_dir():
        log("migrate", f"Moving existing memory from {link}")
        target.mkdir(parents=True, exist_ok=True)
        for f in link.iterdir():
            if f.is_dir():
                continue
            dest = target / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                log("migrate", f"  copied {f.name}")
            elif not filecmp.cmp(f, dest, shallow=False):
                merge_conflicting_file(local=f, repo=dest)
            else:
                log("ok", f"  {f.name} identical, skipping")
        shutil.rmtree(link)

    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
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

    canonical_raws: dict[str, list[str]] = {}
    for raw, canonical in mapping.items():
        canonical_raws.setdefault(canonical, []).append(raw)

    for canonical, raw_names in canonical_raws.items():
        canonical_dir = projects_dir / canonical
        if canonical_dir.exists():
            continue
        for raw_name in raw_names:
            raw_dir = projects_dir / raw_name
            if raw_dir.exists() and raw_dir.is_dir():
                raw_dir.rename(canonical_dir)
                log("migrate", f"Renamed repo dir: {raw_name} -> {canonical}")
                break


def setup_claude_code_symlinks() -> None:
    """Symlink global and project memories, plus CLAUDE.md files."""
    make_symlink(CLAUDE_DIR / "memory", REPO_DIR / "global")
    make_file_symlink(CLAUDE_DIR / "CLAUDE.md", REPO_DIR / "global" / "CLAUDE.md")

    mapping = load_project_mappings()
    migrate_repo_to_canonical(mapping)

    claude_projects_dir = CLAUDE_DIR / "projects"
    if claude_projects_dir.exists():
        for project_dir in claude_projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            raw_name = project_dir.name
            memory_path = project_dir / "memory"

            if memory_path.is_symlink():
                continue

            if raw_name in mapping:
                canonical = mapping[raw_name]
                canonical_dir = REPO_DIR / "projects" / canonical
                make_symlink(memory_path, canonical_dir)
                make_file_symlink(
                    project_dir / "CLAUDE.md",
                    canonical_dir / "CLAUDE.md",
                )
            elif memory_path.is_dir():
                pending_dir = REPO_DIR / "projects" / ".pending" / raw_name
                pending_dir.mkdir(parents=True, exist_ok=True)
                make_symlink(memory_path, pending_dir)
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

    for raw_name, canonical in mapping.items():
        claude_project_dir = claude_projects_dir / raw_name
        memory_path = claude_project_dir / "memory"
        if memory_path.exists():
            continue
        canonical_dir = REPO_DIR / "projects" / canonical
        if canonical_dir.exists():
            claude_project_dir.mkdir(parents=True, exist_ok=True)
            make_symlink(memory_path, canonical_dir)
            make_file_symlink(
                claude_project_dir / "CLAUDE.md",
                canonical_dir / "CLAUDE.md",
            )
            log("ok", f"Pre-linked {raw_name} -> {canonical}")


def setup_generic_target(name: str, source: Path, repo_subdir: str,
                         file_patterns: list[str], exclude: list[str]) -> None:
    """Set up a generic sync target: copy files into repo, replace with symlinks."""
    import fnmatch

    repo_dir = REPO_DIR / repo_subdir

    if not source.exists():
        log("skip", f"[{name}] Source {source} does not exist")
        return

    log("info", f"[{name}] Setting up: {source} -> {repo_dir}")
    repo_dir.mkdir(parents=True, exist_ok=True)

    def should_exclude(item_name: str) -> bool:
        for pattern in exclude:
            if fnmatch.fnmatch(item_name, pattern.rstrip("/")):
                return True
        return False

    def adopt_dir(src: Path, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for item in sorted(src.iterdir()):
            if should_exclude(item.name):
                log("skip", f"  [{name}] Excluded: {item.name}")
                continue
            if item.is_symlink():
                continue
            if item.is_dir():
                adopt_dir(item, dest / item.name)
                continue
            if not any(fnmatch.fnmatch(item.name, pat) for pat in file_patterns):
                continue

            repo_file = dest / item.name
            if not repo_file.exists():
                shutil.copy2(item, repo_file)
                log("migrate", f"  [{name}] Copied {item.name}")
            elif not filecmp.cmp(item, repo_file, shallow=False):
                merge_conflicting_file(local=item, repo=repo_file)
            else:
                log("ok", f"  [{name}] {item.name} identical")

            item.unlink()
            if IS_WINDOWS:
                try:
                    os.symlink(repo_file, item)
                    log("ok", f"  [{name}] Symlinked {item.name}")
                except OSError:
                    shutil.copy2(repo_file, item)
                    log("warn", f"  [{name}] Symlink failed for {item.name}, copied instead")
            else:
                os.symlink(repo_file, item)
                log("ok", f"  [{name}] Symlinked {item.name}")

        # Symlink repo files not in source (from other devices)
        for repo_file in sorted(dest.iterdir()):
            if repo_file.is_dir():
                src_sub = src / repo_file.name
                if not src_sub.exists():
                    src_sub.mkdir(parents=True, exist_ok=True)
                if not src_sub.is_symlink():
                    adopt_dir(src_sub, repo_file)
                continue
            if not any(fnmatch.fnmatch(repo_file.name, pat) for pat in file_patterns):
                continue
            src_file = src / repo_file.name
            if not src_file.exists():
                os.symlink(repo_file, src_file)
                log("ok", f"  [{name}] Symlinked {repo_file.name} (from repo)")

    adopt_dir(source, repo_dir)


def load_sync_conf_targets() -> list[dict]:
    """Load target definitions from sync.conf."""
    config_path = REPO_DIR / "sync.conf"
    if not config_path.exists():
        return []

    cfg = ConfigParser()
    cfg.read(config_path)

    targets = []
    for section in cfg.sections():
        if not section.startswith("target:"):
            continue
        name = section.split(":", 1)[1]
        tdef = {"name": name}
        tdef["source"] = Path(cfg.get(section, "source")).expanduser()
        tdef["repo_subdir"] = cfg.get(section, "repo_subdir")

        if cfg.has_option(section, "mapping_conf"):
            tdef["type"] = "claude-code"
        else:
            tdef["type"] = "generic"

        if cfg.has_option(section, "file_patterns"):
            tdef["file_patterns"] = [p.strip() for p in cfg.get(section, "file_patterns").split(",")]
        else:
            tdef["file_patterns"] = ["*.md"]

        if cfg.has_option(section, "exclude"):
            tdef["exclude"] = [e.strip() for e in cfg.get(section, "exclude").split(",")]
        else:
            tdef["exclude"] = []

        targets.append(tdef)

    return targets


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

    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        check=False,
    )

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
        install_task_scheduler(sys.executable)
    else:
        install_systemd_service()


def git_push_migrated(stage_dirs: list[str]) -> None:
    """Commit and push any migrated files."""
    subprocess.run(["git", "-C", str(REPO_DIR), "add"] + stage_dirs, check=False)
    result = subprocess.run(
        ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"],
        check=False,
    )
    if result.returncode != 0:
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
    print("=== Mesy: Multi-Target Memory Sync Setup ===")
    print(f"Platform: {platform.system()}")
    print(f"Repo:     {REPO_DIR}")
    print(f"Claude:   {CLAUDE_DIR}")
    print()

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # Load targets from sync.conf
    target_defs = load_sync_conf_targets()
    stage_dirs = ["global/", "projects/"]

    # Setup Claude Code target (always runs)
    print("--- Claude Code ---")
    setup_claude_code_symlinks()

    # Setup generic targets from config
    for tdef in target_defs:
        if tdef["type"] == "generic":
            print(f"\n--- {tdef['name']} ---")
            setup_generic_target(
                name=tdef["name"],
                source=tdef["source"],
                repo_subdir=tdef["repo_subdir"],
                file_patterns=tdef["file_patterns"],
                exclude=tdef["exclude"],
            )
            # Add this target's repo subdir to stage dirs
            subdir = tdef["repo_subdir"]
            if not subdir.endswith("/"):
                subdir += "/"
            if subdir not in stage_dirs:
                stage_dirs.append(subdir)

    install_daemon()
    git_push_migrated(stage_dirs)

    print()
    print("=== Setup complete ===")
    print("Memory sync daemon is running. Changes will auto-sync to GitHub.")
    if IS_WINDOWS:
        print("Check status: schtasks /Query /TN MesyClaudeMemorySync")
    else:
        print("Check status: systemctl --user status claude-memory-sync")


if __name__ == "__main__":
    main()
