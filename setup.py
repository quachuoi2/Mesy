#!/usr/bin/env python3
"""Mesy — Multi-target memory sync setup (cross-platform).

Run from inside the cloned Mesy repo on any new system:
    python setup.py

Walks the user through registering one or more data repos in
`REPO_DIR/repos.conf`, performs the initial filesystem migration
(Claude Code symlinks + generic targets) for each repo scoped to its
own `sync.conf`, and installs the background daemon.
"""

import filecmp
import os
import platform
import shutil
import socket
import subprocess
import sys
import argparse
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_project_mappings, load_repos, load_config, load_targets
from src.pending import add_pending

REPO_DIR = Path(__file__).resolve().parent
REPOS_CONF_PATH = REPO_DIR / "repos.conf"
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    CLAUDE_DIR = Path(os.environ["USERPROFILE"]) / ".claude"
else:
    CLAUDE_DIR = Path.home() / ".claude"


SYNC_CONF_TEMPLATE = """\
# Mesy per-repo sync config. See docs for full options.

[mesy]
debounce_seconds = 30
pull_interval_seconds = 60
scan_interval_seconds = 60

# Example Claude Code target (uncomment and edit):
# [target:claude_code]
# source = ~/.claude
# repo_subdir = claude
# mapping_conf = claude/projects.conf
# sync_mode = symlink

# Example Codex target (uncomment to enable):
# [target:codex]
# type = codex
# repo_subdir = codex
# sync_mode = symlink
# export_memories = false
# skills = my-personal-skill
"""


def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


# ---------------------------------------------------------------------------
# repos.conf management
# ---------------------------------------------------------------------------


def _read_repos_conf() -> ConfigParser:
    cfg = ConfigParser()
    if REPOS_CONF_PATH.exists():
        cfg.read(REPOS_CONF_PATH)
    return cfg


def _write_repos_conf(cfg: ConfigParser) -> None:
    with REPOS_CONF_PATH.open("w") as f:
        cfg.write(f)


def _setup_git_sync(data_dir: Path) -> None:
    """Prompt for a git remote URL and initialize sync for `data_dir`.

    Skips if the directory is already a git repo. If the user gives a blank
    URL, warns that sync won't run until git is configured manually.
    """
    if (data_dir / ".git").exists():
        log("ok", f"{data_dir} is already a git repo — skipping remote setup")
        return

    url = input(
        "Git remote URL for this repo (e.g. git@github.com:you/repo.git, blank to skip): "
    ).strip()
    if not url:
        log("warn", f"No remote configured — {data_dir} will not sync until you set one up")
        return

    is_empty = not any(data_dir.iterdir())
    if is_empty:
        data_dir.rmdir()
        result = subprocess.run(
            ["git", "clone", url, str(data_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log("error", f"git clone failed: {result.stderr.strip()}")
            data_dir.mkdir(parents=True, exist_ok=True)
            return
        log("ok", f"Cloned {url} into {data_dir}")
        return

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(data_dir), *args],
            capture_output=True, text=True,
        )

    if _git("init").returncode != 0:
        log("error", "git init failed")
        return
    if _git("remote", "add", "origin", url).returncode != 0:
        log("error", "git remote add failed")
        return

    fetch = _git("fetch", "origin")
    if fetch.returncode != 0:
        log("warn", f"git fetch failed: {fetch.stderr.strip()} — leaving repo initialized")
        return

    # Determine remote default branch (fall back to main).
    head = _git("symbolic-ref", "refs/remotes/origin/HEAD")
    if head.returncode == 0 and head.stdout.strip():
        branch = head.stdout.strip().rsplit("/", 1)[-1]
    else:
        branch = "main"

    _git("checkout", "-b", branch)
    pull = _git("pull", "origin", branch, "--allow-unrelated-histories")
    if pull.returncode != 0:
        log("warn", f"git pull failed: {pull.stderr.strip()}")
        return

    _git("branch", f"--set-upstream-to=origin/{branch}", branch)
    log("ok", f"Initialized {data_dir} with remote {url} (branch {branch})")


def _repo_path_valid(raw: str) -> Path | None:
    """Resolve the user-provided path. Reject if it resolves inside REPO_DIR.

    Returns the resolved Path on success, None on rejection (caller re-prompts).
    """
    candidate = Path(raw).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as e:
        log("error", f"Cannot resolve path: {e}")
        return None

    repo_resolved = REPO_DIR.resolve()
    try:
        resolved.relative_to(repo_resolved)
        log("error", f"Data repo path resolves inside the Mesy code dir: {resolved}")
        return None
    except ValueError:
        pass

    return resolved


def prompt_repos_conf() -> list[dict]:
    """Show existing repos, let user add more, persist to repos.conf.

    Returns the full list of repos (existing + new) as dicts with
    keys 'name' and 'path' (same shape as src.config.load_repos).
    """
    existing = load_repos()

    if existing:
        print(f"Found {len(existing)} repo(s) in repos.conf:")
        for r in existing:
            print(f"  [{r['name']}] {r['path']}")
        if input("Add another data repo? [y/N]: ").strip().lower() != "y":
            return existing
    else:
        print("No data repos registered yet. Add at least one.")

    cfg = _read_repos_conf()
    existing_names = {r["name"] for r in existing}
    new_repos: list[dict] = []

    while True:
        name = input("Repo name (e.g. alfred-memory): ").strip()
        if not name:
            log("error", "Name cannot be empty")
            continue
        if name in existing_names or any(r["name"] == name for r in new_repos):
            log("error", f"Name '{name}' already registered")
            continue

        while True:
            raw_path = input("Repo path: ").strip()
            if not raw_path:
                log("error", "Path cannot be empty")
                continue
            resolved = _repo_path_valid(raw_path)
            if resolved is None:
                continue

            if not resolved.exists():
                create = input(f"Directory {resolved} does not exist. Create it? [Y/n]: ").strip().lower()
                if create in ("", "y"):
                    resolved.mkdir(parents=True, exist_ok=True)
                else:
                    log("error", "Skipping — path must exist")
                    continue
            elif not resolved.is_dir():
                log("error", f"{resolved} is not a directory")
                continue

            break

        _setup_git_sync(resolved)

        section = f"repo:{name}"
        cfg[section] = {"path": str(resolved)}
        new_repos.append({"name": name, "path": resolved})
        log("ok", f"Queued [{section}] path = {resolved}")

        if input("Add another data repo? [y/N]: ").strip().lower() != "y":
            break

    if new_repos:
        _write_repos_conf(cfg)
        log("ok", f"Wrote {len(new_repos)} new repo(s) to repos.conf")

    return existing + new_repos


# ---------------------------------------------------------------------------
# sync.conf handling (per-repo)
# ---------------------------------------------------------------------------


def ensure_sync_conf(data_dir: Path) -> bool:
    """Verify data_dir has a sync.conf. Offer to create a template if missing.

    Returns True if the file exists (or was created), False if the user
    declined and the repo should be skipped.
    """
    sync_conf = data_dir / "sync.conf"
    if sync_conf.exists():
        return True

    log("warn", f"{sync_conf} is missing")
    create = input("Create a template sync.conf? [Y/n]: ").strip().lower()
    if create in ("", "y"):
        data_dir.mkdir(parents=True, exist_ok=True)
        sync_conf.write_text(SYNC_CONF_TEMPLATE)
        log("ok", f"Wrote template sync.conf to {sync_conf} — edit it before running the daemon")
        return True

    log("skip", f"Skipping {data_dir} — no sync.conf")
    return False


def load_sync_conf_targets(data_dir: Path) -> list[dict]:
    """Load target definitions from a repo's sync.conf."""
    return load_targets(load_config(data_dir))


# ---------------------------------------------------------------------------
# File helpers (unchanged from original — pure, no globals)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Per-repo migration
# ---------------------------------------------------------------------------


def migrate_repo_to_canonical(data_dir: Path, mapping: dict[str, str]) -> None:
    """Rename raw-named project dirs in the repo to their canonical names."""
    projects_dir = data_dir / "claude" / "projects"
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


def setup_claude_code_symlinks(data_dir: Path) -> None:
    """Symlink global and project memories, plus CLAUDE.md files, for one data repo."""
    make_symlink(CLAUDE_DIR / "memory", data_dir / "claude" / "global")
    make_file_symlink(CLAUDE_DIR / "CLAUDE.md", data_dir / "claude" / "global" / "CLAUDE.md")

    mapping = load_project_mappings(data_dir)
    migrate_repo_to_canonical(data_dir, mapping)

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
                canonical_dir = data_dir / "claude" / "projects" / canonical
                make_symlink(memory_path, canonical_dir)
                make_file_symlink(
                    project_dir / "CLAUDE.md",
                    canonical_dir / "CLAUDE.md",
                )
            elif memory_path.is_dir():
                add_pending(data_dir, raw_name)
                log("pending", f"{raw_name} has no mapping — recorded in .pending file")
                log("info", f"  Add to projects.conf: [YourProjectName] / paths = {raw_name}")

    for raw_name, canonical in mapping.items():
        claude_project_dir = claude_projects_dir / raw_name
        memory_path = claude_project_dir / "memory"
        if memory_path.exists():
            continue
        canonical_dir = data_dir / "claude" / "projects" / canonical
        if canonical_dir.exists():
            claude_project_dir.mkdir(parents=True, exist_ok=True)
            make_symlink(memory_path, canonical_dir)
            make_file_symlink(
                claude_project_dir / "CLAUDE.md",
                canonical_dir / "CLAUDE.md",
            )
            log("ok", f"Pre-linked {raw_name} -> {canonical}")


def setup_generic_target(data_dir: Path, name: str, source: Path, repo_subdir: str,
                         file_patterns: list[str], exclude: list[str]) -> None:
    """Set up a generic sync target: copy files into repo, replace with symlinks."""
    import fnmatch

    repo_dir = data_dir / repo_subdir

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
            if src_file.is_symlink():
                if src_file.resolve() == repo_file.resolve():
                    continue
                src_file.unlink()
            elif src_file.exists():
                continue
            os.symlink(repo_file, src_file)
            log("ok", f"  [{name}] Symlinked {repo_file.name} (from repo)")

    adopt_dir(source, repo_dir)


def setup_repo(data_dir: Path, seen_target_types: set[str]) -> None:
    """Run the per-repo migration: Claude Code + generic targets.

    `seen_target_types` is mutated to track which target types have already
    been claimed by an earlier repo. Duplicates across repos are skipped
    with a warning.
    """
    target_defs = load_sync_conf_targets(data_dir)

    for tdef in target_defs:
        ttype = tdef["type"]
        # Legacy Claude setup always claims the same ~/.claude directory.
        # Generic and Codex instances are deduplicated by source ownership instead.
        key = ttype if ttype == "claude-code" else f"{ttype}:{tdef['source'].absolute()}"
        if key in seen_target_types:
            log("warn", f"[{tdef['name']}] target type '{ttype}' already handled by an earlier repo — skipping")
            continue
        seen_target_types.add(key)

        if ttype == "codex":
            from src.main import build_targets
            target = build_targets([tdef], data_dir)[0]
            for line in target.preview():
                log("codex", line)
            target.setup()
            continue

        if ttype == "claude-code":
            print(f"--- Claude Code ({data_dir.name}) ---")
            setup_claude_code_symlinks(data_dir)
            continue

        if ttype == "generic":
            if tdef.get("sync_mode") == "copy":
                log(
                    "info",
                    f"[{tdef['name']}] Copy-mode target — skipping symlink setup. "
                    "Will be initialized on first daemon run.",
                )
                continue
            print(f"--- {tdef['name']} ({data_dir.name}) ---")
            setup_generic_target(
                data_dir=data_dir,
                name=tdef["name"],
                source=tdef["source"],
                repo_subdir=tdef["repo_subdir"],
                file_patterns=tdef["file_patterns"],
                exclude=tdef["exclude"],
            )


# ---------------------------------------------------------------------------
# Daemon install (unchanged)
# ---------------------------------------------------------------------------


def install_systemd_service() -> None:
    """Install and enable the systemd user service (Linux only)."""
    service_template = REPO_DIR / "mesy.service"
    if not service_template.exists():
        log("skip", "Service template not found")
        return

    user_service_dir = Path.home() / ".config" / "systemd" / "user"
    user_service_dir.mkdir(parents=True, exist_ok=True)

    service_content = service_template.read_text().replace("%REPO_DIR%", str(REPO_DIR))
    dest = user_service_dir / "mesy.service"
    dest.write_text(service_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "mesy"], check=False
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
    """Install a Windows Task Scheduler task for the sync daemon.

    Uses PowerShell's Register-ScheduledTask instead of schtasks /Create
    because ONLOGON triggers via schtasks require admin; the PS cmdlet
    creates a per-user ONLOGON task without elevation.
    """
    task_name = "Mesy"
    sync_script = REPO_DIR / "sync.py"

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=== Mesy: Multi-Target Memory Sync Setup ===")
    print(f"Platform: {platform.system()}")
    print(f"Repo:     {REPO_DIR}")
    print(f"Claude:   {CLAUDE_DIR}")
    print()

    repos = prompt_repos_conf()
    if not repos:
        raise SystemExit("[error] No data repos registered; aborting.")

    print()
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    seen_target_types: set[str] = set()
    # Validate every configured target before any repository is migrated.
    from src.main import _build_repo_contexts
    _build_repo_contexts(repos)
    for repo in repos:
        data_dir = repo["path"]
        print(f"\n=== Repo: {repo['name']} ({data_dir}) ===")
        if not ensure_sync_conf(data_dir):
            continue
        setup_repo(data_dir, seen_target_types)

    print()
    install_daemon()

    print()
    print("=== Setup complete ===")
    print("Memory sync daemon is running. Changes will auto-sync to GitHub.")
    if IS_WINDOWS:
        print("Check status: schtasks /Query /TN Mesy")
    else:
        print("Check status: systemctl --user status mesy")


def configured_codex(action: str, data_dir: Path | None = None) -> None:
    """Preview, adopt, or detach configured Codex targets without installing a daemon."""
    from src.main import _build_repo_contexts
    from src.targets.codex import CodexTarget
    repos = ([{"name": data_dir.name, "path": data_dir.resolve()}]
             if data_dir else load_repos())
    contexts = _build_repo_contexts(repos)
    targets = [t for ctx in contexts for t in ctx.targets if isinstance(t, CodexTarget)]
    if not targets:
        raise SystemExit("No Codex targets configured. Add [target:codex] with type = codex first.")
    for target in targets:
        for line in target.preview():
            log("codex", line)
        if action == "adopt":
            target.setup()
        elif action == "detach":
            target.detach()
            log("info", "Disable the Codex target before restarting Mesy to prevent relinking.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", choices=["preview", "adopt", "detach"], help="Manage only configured Codex targets; no Git writes or service installation")
    parser.add_argument("--data-dir", type=Path, help="Data repo used with --codex")
    args = parser.parse_args()
    if args.codex:
        configured_codex(args.codex, args.data_dir)
    elif args.data_dir:
        parser.error("--data-dir requires --codex")
    else:
        main()
