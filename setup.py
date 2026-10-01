#!/usr/bin/env python3
"""Mesy — Multi-target memory sync setup (cross-platform).

Run from inside the cloned Mesy repo on any new system:
    python setup.py

Walks the user through registering one or more data repos in
`MESY_DIR/repos.ini`, performs the initial filesystem migration
(Claude Code symlinks + generic targets) for each repo scoped to its
own `sync.ini`, and installs the background daemon.
"""

import filecmp
import logging
import os
import platform
import shutil
import subprocess
import sys
from configparser import ConfigParser
from pathlib import Path

from src.config import (
    MESY_DIR,
    REPOS_CFG_PATH,
    SETTING_DEFAULTS,
    load_repos,
    load_sync_cfg,
    load_targets,
)
from src.git_sync import DEFAULT_BRANCH, git
from src.pending import add_pending
from src.conflict import merge_conflicting_file
from src.sync_modes.symlink import all_copied, merge_into
from src.targets.claude_code import CLAUDE_DIR, load_project_mappings
from src.targets.generic import is_excluded, matches_patterns

IS_WINDOWS = platform.system() == "Windows"

_SETTING_LINES = "\n".join(f"{key} = {value}" for key, value in SETTING_DEFAULTS.items())
SYNC_CFG_TEMPLATE = f"""\
# Mesy per-repo sync config. See docs for full options.

[mesy]
{_SETTING_LINES}

# Example Claude Code target (uncomment and edit):
# [target:claude-code]
# source = ~/.claude
# repo_subdir = claude
# sync_mode = symlink

# Example Codex target (uncomment to enable):
# [target:codex]
# repo_subdir = codex
# sync_mode = symlink
"""


def print_status(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


def _read_repos_cfg() -> ConfigParser:
    repos_cfg = ConfigParser()
    if REPOS_CFG_PATH.exists():
        repos_cfg.read(REPOS_CFG_PATH)
    return repos_cfg


def _write_repos_cfg(repos_cfg: ConfigParser) -> None:
    with REPOS_CFG_PATH.open("w") as f:
        repos_cfg.write(f)


def _setup_git_sync(repo_base: Path) -> None:
    """Prompt for a git remote URL and initialize sync for `repo_base`.

    Skips if the directory is already a git repo. If the user gives a blank
    URL, warns that sync won't run until git is configured manually.
    """
    if (repo_base / ".git").exists():
        print_status("ok", f"{repo_base} is already a git repo — skipping remote setup")
        return

    url = input(
        "Git remote URL for this repo (e.g. git@github.com:you/repo.git, blank to skip): "
    ).strip()
    if not url:
        print_status("warn", f"No remote configured — {repo_base} will not sync until you set one up")
        return

    if not any(repo_base.iterdir()):
        _clone(repo_base, url)
    else:
        _init_with_remote(repo_base, url)


def _clone(repo_base: Path, url: str) -> None:
    repo_base.rmdir()
    result = subprocess.run(
        ["git", "clone", url, str(repo_base)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print_status("error", f"git clone failed: {result.stderr.strip()}")
        repo_base.mkdir(parents=True, exist_ok=True)
        return

    print_status("ok", f"Cloned {url} into {repo_base}")


def _init_with_remote(repo_base: Path, url: str) -> None:
    if git("init", repo_base=repo_base).returncode != 0:
        print_status("error", "git init failed")
        return

    if git("remote", "add", "origin", url, repo_base=repo_base).returncode != 0:
        print_status("error", "git remote add failed")
        return

    fetch_result = git("fetch", "origin", repo_base=repo_base)
    if fetch_result.returncode != 0:
        print_status("warn", f"git fetch failed: {fetch_result.stderr.strip()} — leaving repo initialized")
        return

    head_result = git("symbolic-ref", "refs/remotes/origin/HEAD", repo_base=repo_base)
    if head_result.returncode == 0 and head_result.stdout.strip():
        branch = head_result.stdout.strip().rsplit("/", 1)[-1]
    else:
        branch = DEFAULT_BRANCH

    git("checkout", "-b", branch, repo_base=repo_base)

    pull_result = git("pull", "origin", branch, "--allow-unrelated-histories", repo_base=repo_base)
    if pull_result.returncode != 0:
        print_status("warn", f"git pull failed: {pull_result.stderr.strip()}")
        return

    git("branch", f"--set-upstream-to=origin/{branch}", branch, repo_base=repo_base)
    print_status("ok", f"Initialized {repo_base} with remote {url} (branch {branch})")


def _repo_path_valid(raw: str) -> Path | None:
    """Resolve the user-provided path. Reject if it resolves inside MESY_DIR.

    Returns the resolved Path on success, None on rejection (caller re-prompts).
    """
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve()

    except OSError as e:
        print_status("error", f"Cannot resolve path: {e}")
        return None

    repo_resolved = MESY_DIR.resolve()
    try:
        resolved.relative_to(repo_resolved)
        print_status("error", f"Data repo path resolves inside the Mesy code dir: {resolved}")
        return None

    except ValueError:
        pass

    return resolved


def prompt_repos_cfg() -> list[dict]:
    """Show existing repos, let user add more, persist to repos.ini.

    Returns the full list of repos (existing + new) as dicts with
    keys 'name' and 'path' (same shape as src.config.load_repos).
    """
    existing = load_repos()
    if not _want_more_repos(existing):
        return existing

    repos_cfg = _read_repos_cfg()
    existing_names = {r["name"] for r in existing}
    new_repos: list[dict] = []

    while True:
        name = _prompt_name(existing_names | {r["name"] for r in new_repos})
        resolved = _prompt_path()

        _setup_git_sync(resolved)

        section = f"repo:{name}"
        repos_cfg[section] = {"path": str(resolved)}
        new_repos.append({"name": name, "path": resolved})
        print_status("ok", f"Queued [{section}] path = {resolved}")

        if input("Add another data repo? [y/N]: ").strip().lower() != "y":
            break

    if new_repos:
        _write_repos_cfg(repos_cfg)
        print_status("ok", f"Wrote {len(new_repos)} new repo(s) to repos.ini")

    return existing + new_repos


def _want_more_repos(existing: list[dict]) -> bool:
    if not existing:
        print("No data repos registered yet. Add at least one.")
        return True

    print(f"Found {len(existing)} repo(s) in repos.ini:")
    for r in existing:
        print(f"  [{r['name']}] {r['path']}")

    return input("Add another data repo? [y/N]: ").strip().lower() == "y"


def _prompt_name(taken: set[str]) -> str:
    while True:
        name = input("Repo name (e.g. alfred-memory): ").strip()
        if not name:
            print_status("error", "Name cannot be empty")
            continue

        if name in taken:
            print_status("error", f"Name '{name}' already registered")
            continue

        return name


def _prompt_path() -> Path:
    while True:
        raw_path = input("Repo path: ").strip()
        if not raw_path:
            print_status("error", "Path cannot be empty")
            continue

        resolved = _repo_path_valid(raw_path)
        if resolved is None:
            continue

        if not resolved.exists():
            create = input(f"Directory {resolved} does not exist. Create it? [Y/n]: ").strip().lower()
            if create in ("", "y"):
                resolved.mkdir(parents=True, exist_ok=True)
            else:
                print_status("error", "Skipping — path must exist")
                continue
        elif not resolved.is_dir():
            print_status("error", f"{resolved} is not a directory")
            continue

        return resolved


def ensure_sync_cfg(repo_base: Path) -> bool:
    """Verify repo_base has a sync.ini. Offer to create a template if missing.

    Returns True if the file exists (or was created), False if the user
    declined and the repo should be skipped.
    """
    sync_cfg_path = repo_base / "sync.ini"
    if sync_cfg_path.exists():
        return True

    print_status("warn", f"{sync_cfg_path} is missing")
    create = input("Create a template sync.ini? [Y/n]: ").strip().lower()
    if create in ("", "y"):
        repo_base.mkdir(parents=True, exist_ok=True)
        sync_cfg_path.write_text(SYNC_CFG_TEMPLATE)
        print_status("ok", f"Wrote template sync.ini to {sync_cfg_path} — edit it before running the daemon")
        return True

    print_status("skip", f"Skipping {repo_base} — no sync.ini")
    return False


def load_sync_cfg_targets(repo_base: Path) -> list[dict]:
    """Load target definitions from a repo's sync.ini."""
    return load_targets(load_sync_cfg(repo_base))


def _already_linked(link: Path, target: Path, label: str) -> bool:
    """True when *link* already points at *target*; a link elsewhere is removed."""
    if not link.is_symlink():
        return False

    if link.resolve() == target.resolve():
        print_status("ok", f"Symlink already exists: {label}")
        return True

    print_status("relink", f"Updating symlink: {label} -> {target}")
    link.unlink()
    return False


def make_file_symlink(link: Path, target: Path) -> None:
    """Create a symlink for a single file, migrating existing content if needed."""
    if _already_linked(link, target, link.name):
        return

    if link.is_file():
        _move_file_into_repo(link, target)
    elif not target.exists():
        return

    link.parent.mkdir(parents=True, exist_ok=True)

    _link_file(link, target, link.name)


def _link_file(link: Path, target: Path, label: str) -> None:
    """Symlink *link* to *target*; on Windows without symlink permission, copy instead."""
    if IS_WINDOWS:
        try:
            os.symlink(target, link)
            print_status("ok", f"Symlinked {label}")

        except OSError:
            shutil.copy2(target, link)
            print_status("warn", f"Symlink failed for {label}, copied instead (edits won't sync back!)")
    else:
        os.symlink(target, link)
        print_status("ok", f"Symlinked {label}")


def _move_file_into_repo(link: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(link, target)
        print_status("migrate", f"Copied {link.name} into repo")
    elif not filecmp.cmp(link, target, shallow=False):
        merge_conflicting_file(local=link, repo=target)
    else:
        print_status("ok", f"{link.name} identical, skipping")

    link.unlink()


def make_symlink(link: Path, target: Path) -> None:
    """Create a symlink for a directory, with Windows-compatible support."""
    if _already_linked(link, target, str(link)):
        return

    if link.is_dir() and not _move_dir_into_repo(link, target):
        return

    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        try:
            os.symlink(target, link, target_is_directory=True)
            print_status("ok", f"Symlinked {link.name}")

        except OSError:
            print_status("warn", "Symlink failed (enable Developer Mode in Windows Settings)")
            print_status("info", "Trying directory junction instead...")
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
            )
            print_status("ok", f"Junction created for {link.name}")
    else:
        os.symlink(target, link)
        print_status("ok", f"Symlinked {link.name}")


def _move_dir_into_repo(link: Path, target: Path) -> bool:
    """Copy a folder, subfolders included, into the repo; delete it only if every file arrived."""
    print_status("migrate", f"Moving existing memory from {link}")
    target.mkdir(parents=True, exist_ok=True)
    merge_into(link, target)

    if not all_copied(link, target):
        print_status("error", f"Not every file reached {target}; leaving {link} in place")
        return False

    shutil.rmtree(link)
    return True


def setup_claude_code_symlinks(repo_base: Path) -> None:
    """Symlink the global CLAUDE.md, rules/ and each project's memory for one data repo."""
    make_file_symlink(CLAUDE_DIR / "CLAUDE.md", repo_base / "claude" / "global" / "CLAUDE.md")
    make_symlink(CLAUDE_DIR / "rules", repo_base / "claude" / "global" / "rules")

    mapping = load_project_mappings(repo_base)

    claude_projects_dir = CLAUDE_DIR / "projects"
    if claude_projects_dir.exists():
        for project_dir in claude_projects_dir.iterdir():
            if project_dir.is_dir():
                _link_project(repo_base, project_dir, mapping)

    _prelink_mapped(repo_base, claude_projects_dir, mapping)


def _link_project(repo_base: Path, project_dir: Path, mapping: dict[str, str]) -> None:
    raw_name = project_dir.name
    memory_path = project_dir / "memory"

    if memory_path.is_symlink():
        return

    if raw_name in mapping:
        _link_to_canonical(project_dir, repo_base / "claude" / "projects" / mapping[raw_name])
    elif memory_path.is_dir():
        add_pending(repo_base, raw_name)
        print_status("pending", f"{raw_name} has no mapping — recorded in .pending file")
        print_status("info", f"  Add to projects.ini: [YourProjectName] / paths = {raw_name}")


def _prelink_mapped(repo_base: Path, claude_projects_dir: Path, mapping: dict[str, str]) -> None:
    for raw_name, canonical in mapping.items():
        claude_project_dir = claude_projects_dir / raw_name
        memory_path = claude_project_dir / "memory"
        if memory_path.exists():
            continue

        canonical_dir = repo_base / "claude" / "projects" / canonical
        if canonical_dir.exists():
            claude_project_dir.mkdir(parents=True, exist_ok=True)
            _link_to_canonical(claude_project_dir, canonical_dir)
            print_status("ok", f"Pre-linked {raw_name} -> {canonical}")


def _link_to_canonical(project_dir: Path, canonical_dir: Path) -> None:
    make_symlink(project_dir / "memory", canonical_dir)


def setup_generic_target(repo_base: Path, name: str, src: Path, repo_subdir: str,
                         file_patterns: list[str], exclude: list[str]) -> None:
    """Set up a generic sync target: copy files into repo, replace with symlinks."""
    target_dir = repo_base / repo_subdir

    if not src.exists():
        print_status("skip", f"[{name}] Source {src} does not exist")
        return

    print_status("info", f"[{name}] Setting up: {src} -> {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    _adopt_dir(src, target_dir, name, file_patterns, exclude)


def _adopt_dir(src: Path, dest: Path, name: str, file_patterns: list[str], exclude: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    for item in sorted(src.iterdir()):
        if is_excluded(item.name, exclude):
            print_status("skip", f"  [{name}] Excluded: {item.name}")
            continue

        if item.is_symlink():
            continue

        if item.is_dir():
            _adopt_dir(item, dest / item.name, name, file_patterns, exclude)
            continue

        if not matches_patterns(item.name, file_patterns):
            continue

        _adopt_file(item, dest / item.name, name)

    for repo_file in sorted(dest.iterdir()):
        _link_repo_item(repo_file, src, name, file_patterns, exclude)


def _adopt_file(item: Path, repo_file: Path, name: str) -> None:
    if not repo_file.exists():
        shutil.copy2(item, repo_file)
        print_status("migrate", f"  [{name}] Copied {item.name}")
    elif not filecmp.cmp(item, repo_file, shallow=False):
        merge_conflicting_file(local=item, repo=repo_file)
    else:
        print_status("ok", f"  [{name}] {item.name} identical")

    item.unlink()
    _link_file(item, repo_file, f"[{name}] {item.name}")


def _link_repo_item(repo_file: Path, src: Path, name: str, file_patterns: list[str],
                    exclude: list[str]) -> None:
    if repo_file.is_dir():
        src_sub = src / repo_file.name
        if not src_sub.exists():
            src_sub.mkdir(parents=True, exist_ok=True)
        if not src_sub.is_symlink():
            _adopt_dir(src_sub, repo_file, name, file_patterns, exclude)
        return

    if not matches_patterns(repo_file.name, file_patterns):
        return

    src_file = src / repo_file.name
    if src_file.is_symlink():
        if src_file.resolve() == repo_file.resolve():
            return
        src_file.unlink()
    elif src_file.exists():
        return

    _link_file(src_file, repo_file, f"[{name}] {repo_file.name} (from repo)")


def setup_repo(repo_base: Path, seen_target_types: set[str]) -> None:
    """Run the per-repo migration: Claude Code + generic targets.

    `seen_target_types` is mutated to track which target types have already
    been claimed by an earlier repo. Duplicates across repos are skipped
    with a warning.
    """
    for target_def in load_sync_cfg_targets(repo_base):
        if not _claim_target(target_def, seen_target_types):
            continue

        if target_def["type"] == "codex":
            _setup_codex(target_def, repo_base)
        elif target_def["type"] == "claude-code":
            print(f"--- Claude Code ({repo_base.name}) ---")
            setup_claude_code_symlinks(repo_base)
        elif target_def["type"] == "generic":
            _setup_generic(target_def, repo_base)


def _claim_target(target_def: dict, seen_target_types: set[str]) -> bool:
    target_type = target_def["type"]
    # Legacy Claude setup always claims the same ~/.claude directory.
    # Generic and Codex instances are deduplicated by source ownership instead.
    key = target_type if target_type == "claude-code" else f"{target_type}:{target_def['source'].absolute()}"
    if key in seen_target_types:
        print_status(
            "warn",
            f"[{target_def['name']}] target type '{target_type}' already handled by an earlier repo — skipping",
        )
        return False

    seen_target_types.add(key)
    return True


def _setup_codex(target_def: dict, repo_base: Path) -> None:
    from src.factory import build_targets
    print(f"--- {target_def['name']} ({repo_base.name}) ---")
    build_targets([target_def], repo_base)[0].setup()


def _setup_generic(target_def: dict, repo_base: Path) -> None:
    if target_def.get("sync_mode") == "copy":
        print_status(
            "info",
            f"[{target_def['name']}] Copy-mode target — skipping symlink setup. "
            "Will be initialized on first daemon run.",
        )
        return

    print(f"--- {target_def['name']} ({repo_base.name}) ---")
    setup_generic_target(
        repo_base=repo_base,
        name=target_def["name"],
        src=target_def["source"],
        repo_subdir=target_def["repo_subdir"],
        file_patterns=target_def["file_patterns"],
        exclude=target_def["exclude"],
    )


def install_systemd_service() -> None:
    """Install and enable the systemd user service (Linux only)."""
    service_template = MESY_DIR / "mesy.service"
    if not service_template.exists():
        print_status("skip", "Service template not found")
        return

    user_service_dir = Path.home() / ".config" / "systemd" / "user"
    user_service_dir.mkdir(parents=True, exist_ok=True)

    service_content = service_template.read_text().replace("%MESY_DIR%", str(MESY_DIR))
    dest = user_service_dir / "mesy.service"
    dest.write_text(service_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "mesy"], check=False
    )
    print_status("ok", "Systemd service installed and started")

    result = subprocess.run(
        ["loginctl", "enable-linger", os.environ.get("USER", "")],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print_status("warn", "Could not enable lingering (may need sudo)")


def install_task_scheduler(python_exe: str) -> None:
    """Install a Windows Task Scheduler task for the sync daemon.

    Uses PowerShell's Register-ScheduledTask instead of schtasks /Create
    because ONLOGON triggers via schtasks require admin; the PS cmdlet
    creates a per-user ONLOGON task without elevation.
    """
    task_name = "Mesy"
    sync_script = MESY_DIR / "sync.py"

    ps_script = (
        f'$action = New-ScheduledTaskAction -Execute \'"{python_exe}"\' -Argument \'"{sync_script}"\';'
        f'$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME;'
        f'Unregister-ScheduledTask -TaskName "{task_name}" -Confirm:$false -ErrorAction SilentlyContinue;'
        f'Register-ScheduledTask -TaskName "{task_name}" -Action $action -Trigger $trigger -RunLevel Limited'
    )

    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        print_status("ok", "Task Scheduler task created (runs at logon)")
        subprocess.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            check=False,
        )
        print_status("ok", "Sync daemon started")
    else:
        print_status("warn", f"Task Scheduler setup failed: {result.stderr.strip()}")
        print_status("info", f"You can run manually: {python_exe} {sync_script}")


def install_daemon() -> None:
    """Install the appropriate background daemon for this platform."""
    if IS_WINDOWS:
        install_task_scheduler(sys.executable)
    else:
        install_systemd_service()


def main() -> None:
    print("=== Mesy: Multi-Target Memory Sync Setup ===")
    print(f"Platform: {platform.system()}")
    print(f"Repo:     {MESY_DIR}")
    print(f"Claude:   {CLAUDE_DIR}")
    print()

    repos = prompt_repos_cfg()
    if not repos:
        raise SystemExit("[error] No data repos registered; aborting.")

    print()
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    _setup_repos(repos)

    print()
    install_daemon()

    print()
    print("=== Setup complete ===")
    print("Memory sync daemon is running. Changes will auto-sync to GitHub.")
    if IS_WINDOWS:
        print("Check status: schtasks /Query /TN Mesy")
    else:
        print("Check status: systemctl --user status mesy")


def _setup_repos(repos: list[dict]) -> None:
    # Validate every configured target before any repository is migrated.
    from src.main import LOG_FMT, build_repo_ctxs
    logging.basicConfig(level=logging.INFO, format=LOG_FMT)
    build_repo_ctxs(repos)

    seen_target_types: set[str] = set()
    for repo_def in repos:
        repo_base = repo_def["path"]
        print(f"\n=== Repo: {repo_def['name']} ({repo_base}) ===")
        if not ensure_sync_cfg(repo_base):
            continue

        setup_repo(repo_base, seen_target_types)


if __name__ == "__main__":
    main()
