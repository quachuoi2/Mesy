"""Mesy main loop — multi-repo, multi-target sync daemon."""

from __future__ import annotations

import logging
import math
import time
import os
import subprocess
from pathlib import Path

from src.config import (
    REPO_DIR,
    RepoContext,
    get_mesy_settings,
    load_config,
    load_repos,
    load_targets,
)
from src.git_utils import commit_and_push, pull_remote
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget
from src.targets.codex import CodexTarget
from src.links import checked_destination
from src.transport import sync_repository

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("mesy")


def build_targets(target_defs: list[dict], data_dir: Path) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions.

    Args:
        target_defs: parsed target definitions from load_targets().
        data_dir: the data repo root — passed to each target so it can
            compute stage dirs and hash file paths without touching globals.
    """
    targets = []
    for tdef in target_defs:
        repo_dir = data_dir / tdef["repo_subdir"]
        checked_destination(data_dir, repo_dir)
        sync_mode = tdef.get("sync_mode", "symlink")
        if tdef["type"] == "codex":
            targets.append(CodexTarget(
                name=tdef["name"], source=tdef["source"], repo_dir=repo_dir,
                data_dir=data_dir, sync_mode=sync_mode,
                **{key: tdef[key] for key in ("skills", "skills_source", "export_memories", "device_id", "export_settle_seconds") if key in tdef},
            ))
        elif tdef["type"] == "claude-code":
            targets.append(ClaudeCodeTarget(
                source=tdef["source"],
                repo_dir=repo_dir,
                data_dir=data_dir,
                sync_mode=sync_mode,
            ))
        elif tdef["type"] == "generic":
            targets.append(GenericTarget(
                name=tdef["name"],
                source=tdef["source"],
                repo_dir=repo_dir,
                data_dir=data_dir,
                file_patterns=tdef.get("file_patterns", ["*.md"]),
                exclude=tdef.get("exclude", []),
                sync_mode=sync_mode,
            ))
        else:
            log.warning("Unknown target type: %s", tdef["type"])
    return targets


def get_all_stage_dirs(targets: list[SyncTarget]) -> list[str]:
    """Collect unique git-add directories from all targets."""
    dirs = []
    seen = set()
    for t in targets:
        for d in t.get_stage_dirs():
            if d not in seen:
                seen.add(d)
                dirs.append(d)
    return dirs


def get_md_mtimes(data_dir: Path) -> dict[str, float]:
    """Get modification times of all .md files in data_dir."""
    mtimes = {}
    for md_file in data_dir.rglob("*.md"):
        if ".git" in md_file.parts:
            continue
        try:
            mtimes[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return mtimes


def _build_repo_contexts(repo_list: list[dict]) -> list[RepoContext]:
    """Build RepoContext objects from a list of repo dicts (from load_repos())."""
    contexts = []
    for repo_info in repo_list:
        data_dir = repo_info["path"]
        cfg = load_config(data_dir=data_dir)
        settings = get_mesy_settings(cfg)
        for key in ("debounce_seconds", "pull_interval_seconds", "scan_interval_seconds"):
            try:
                value = float(settings[key])
            except (TypeError, ValueError):
                raise ValueError(f"[{repo_info['name']}] {key} must be a number, got {settings[key]!r}") from None
            # nan passes every comparison as False, which would stop the periodic scan forever.
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"[{repo_info['name']}] {key} must be a finite positive number, got {settings[key]!r}")
        target_defs = load_targets(cfg)
        targets = build_targets(target_defs, data_dir=data_dir)
        stage_dirs = get_all_stage_dirs(targets)
        contexts.append(RepoContext(
            name=repo_info["name"],
            data_dir=data_dir,
            targets=targets,
            stage_dirs=stage_dirs,
            settings=settings,
        ))
    validate_ownership(contexts)
    return contexts


def validate_ownership(contexts: list[RepoContext]) -> None:
    """Reject targets claiming the same local path or overlapping destinations."""
    claims: list[tuple[Path, SyncTarget]] = []
    destinations: list[tuple[Path, SyncTarget]] = []
    for ctx in contexts:
        for target in ctx.targets:
            sources = ([source for source, _, _ in target.bindings()]
                       if isinstance(target, CodexTarget) else [target.source])
            for source in sources:
                # Do not resolve the final link: it may already point into the repo.
                path = source.parent.resolve() / source.name
                for previous, owner in claims:
                    if path == previous or path in previous.parents or previous in path.parents:
                        raise ValueError(f"Overlapping sources: {owner.name} ({previous}) and {target.name} ({path})")
                claims.append((path, target))
            destination = target.repo_dir.resolve()
            for previous, owner in destinations:
                if destination == previous or destination in previous.parents or previous in destination.parents:
                    raise ValueError(f"Overlapping destinations: {owner.name} and {target.name}")
            destinations.append((destination, target))
    for source, owner in claims:
        for destination, other in destinations:
            if source == destination or source in destination.parents or destination in source.parents:
                if isinstance(owner, CodexTarget) or isinstance(other, CodexTarget):
                    raise ValueError(f"Source/destination overlap: {source} and {destination}")


def _has_codex(ctx: RepoContext) -> bool:
    return any(isinstance(t, CodexTarget) for t in ctx.targets)


_scan_failures: dict[tuple[str, str], str] = {}


def _scan_context(ctx: RepoContext) -> bool:
    okay = True
    for target in ctx.targets:
        key = (ctx.name, target.name)
        try:
            target.scan()
            if _scan_failures.pop(key, None):
                log.info("[%s/%s] Scan recovered", ctx.name, target.name)
        except (OSError, ValueError, RuntimeError) as exc:
            okay = False
            # An unchanged failure repeats every scan; log it once until it changes.
            if _scan_failures.get(key) != str(exc):
                _scan_failures[key] = str(exc)
                log.error("[%s/%s] Scan deferred, blocking commits for this repo: %s",
                          ctx.name, target.name, exc)
        except AssertionError:
            okay = False
            _scan_failures.pop(key, None)
            log.exception("[%s/%s] Scan hit an internal error", ctx.name, target.name)
    return okay


def _commit_context(ctx: RepoContext) -> None:
    try:
        if _has_codex(ctx):
            if sync_repository(ctx.data_dir, ctx.stage_dirs):
                _scan_context(ctx)
        else:
            commit_and_push(ctx.stage_dirs, data_dir=ctx.data_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[%s] Git operation deferred: %s", ctx.name, exc)


def _pull_context(ctx: RepoContext) -> None:
    if _has_codex(ctx):
        # This also retries committed-but-unpushed work without new filesystem events.
        if _scan_context(ctx):
            _commit_context(ctx)
    else:
        try:
            pull_remote(data_dir=ctx.data_dir)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("[%s] Pull deferred: %s", ctx.name, exc)


def _event_relevant(ctx: RepoContext, path: str) -> bool:
    candidate = Path(os.path.abspath(path))
    if any(p in {".git", ".mesy-local"} or p.startswith(".mesy-") for p in candidate.parts):
        return False
    paths = [ctx.data_dir / p for p in ctx.stage_dirs]
    for target in ctx.targets:
        if isinstance(target, CodexTarget):
            paths.extend(source for source, _, _ in target.bindings())
            if target.export_memories:
                paths.append(target.source / "memories")
        else:
            paths.append(target.source)
    if not paths:
        return candidate.suffix == ".md"
    return any(candidate == p or p in candidate.parents for p in paths)


def _snapshot(ctx: RepoContext) -> dict:
    # Keep the legacy Markdown scan for repositories without configured targets.
    if not ctx.targets:
        return get_md_mtimes(ctx.data_dir)
    paths = [ctx.data_dir / p for p in ctx.stage_dirs]
    for target in ctx.targets:
        if isinstance(target, CodexTarget):
            paths.extend(source for source, _, _ in target.bindings())
            if target.export_memories:
                paths.append(target.source / "memories")
        else:
            paths.append(target.source)
    result = {}
    for root in paths:
        entries = [root]
        if root.is_dir() and not root.is_symlink():
            entries.extend(root.rglob("*"))
        for path in entries:
            if not _event_relevant(ctx, str(path)):
                continue
            try:
                stat = path.lstat()
                if path.is_symlink():
                    result[str(path)] = ("link", os.readlink(path))
                elif path.is_file():
                    result[str(path)] = (stat.st_mtime_ns, stat.st_size, stat.st_mode)
            except OSError:
                pass
    return result


def run_with_watchdog(repo_contexts: list[RepoContext], settings: dict) -> None:
    """Use watchdog for file-change detection across all repos."""
    from watchdog.events import FileSystemEventHandler
    if Observer is None:
        raise ImportError("watchdog is not installed")

    # Per-repo last-change timestamp, keyed by repo name.
    last_change: dict[str, float] = {ctx.name: 0.0 for ctx in repo_contexts}

    class TargetChangeHandler(FileSystemEventHandler):
        def __init__(self, context: RepoContext) -> None:
            self.context = context
            self._repo_name = context.name

        def on_any_event(self, event):
            if event.event_type not in {"created", "modified", "moved", "deleted"}:
                return
            if any(_event_relevant(self.context, p) for p in (event.src_path, getattr(event, "dest_path", "")) if p):
                last_change[self._repo_name] = time.time()

    observer = Observer()
    for ctx in repo_contexts:
        ctx_debounce = float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"]))
        handler = TargetChangeHandler(ctx)
        observer.schedule(handler, str(ctx.data_dir), recursive=True)
        extra_paths = {p for t in ctx.targets for p in t.get_watched_paths() if p.exists()}
        for path in extra_paths:
            if path != ctx.data_dir and ctx.data_dir not in path.parents:
                observer.schedule(handler, str(path), recursive=True)
        log.info(
            "Watching [%s] with watchdog (debounce=%ss, data_dir=%s)",
            ctx.name,
            ctx_debounce,
            ctx.data_dir,
        )

    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = {ctx.name: time.time() for ctx in repo_contexts}

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()

            for ctx in repo_contexts:
                ctx_debounce = float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"]))
                ctx_pull_interval = float(ctx.settings.get("pull_interval_seconds", settings["pull_interval_seconds"]))

                if last_change[ctx.name] and (now - last_change[ctx.name]) > ctx_debounce:
                    last_change[ctx.name] = 0.0
                    if _scan_context(ctx):
                        _commit_context(ctx)

                if (now - last_pull[ctx.name]) > ctx_pull_interval:
                    last_pull[ctx.name] = now
                    _pull_context(ctx)
                scan_interval = float(ctx.settings.get("scan_interval_seconds", settings["scan_interval_seconds"]))
                if now - last_scan[ctx.name] > scan_interval:
                    last_scan[ctx.name] = now
                    _scan_context(ctx)

    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def run_with_polling(repo_contexts: list[RepoContext], settings: dict) -> None:
    """Fallback: poll for changes using mtime comparison, across all repos."""
    # Use the minimum debounce across repos as the tick interval so no repo's
    # debounce window is silently skipped.
    min_debounce = min(
        max(min(float(ctx.settings.get(key, settings[key])) for key in
                ("debounce_seconds", "scan_interval_seconds", "pull_interval_seconds")), 1)
        for ctx in repo_contexts
    ) if repo_contexts else max(float(settings["debounce_seconds"]), 60)
    poll_interval = min_debounce

    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes: dict[str, dict[str, float]] = {
        ctx.name: _snapshot(ctx) for ctx in repo_contexts
    }
    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    initial_scan = time.time()
    last_scan = {ctx.name: initial_scan for ctx in repo_contexts}
    pending = {ctx.name: None for ctx in repo_contexts}

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()

            for ctx in repo_contexts:
                ctx_pull_interval = float(ctx.settings.get("pull_interval_seconds", settings["pull_interval_seconds"]))

                current_mtimes = _snapshot(ctx)
                if current_mtimes != prev_mtimes[ctx.name]:
                    prev_mtimes[ctx.name] = current_mtimes
                    if _has_codex(ctx):
                        pending[ctx.name] = now
                    elif _scan_context(ctx):
                        _commit_context(ctx)
                        prev_mtimes[ctx.name] = _snapshot(ctx)
                debounce = float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"]))
                if pending[ctx.name] is not None and now - pending[ctx.name] >= debounce:
                    pending[ctx.name] = None
                    if _scan_context(ctx):
                        _commit_context(ctx)
                        prev_mtimes[ctx.name] = _snapshot(ctx)

                if (now - last_pull[ctx.name]) > ctx_pull_interval:
                    last_pull[ctx.name] = now
                    _pull_context(ctx)
                scan_interval = float(ctx.settings.get("scan_interval_seconds", settings["scan_interval_seconds"]))
                if now - last_scan[ctx.name] > scan_interval:
                    last_scan[ctx.name] = now
                    _scan_context(ctx)

    except KeyboardInterrupt:
        pass


def main() -> None:
    # Misconfiguration exits non-zero: a plain return reports success, and
    # systemd's Restart=on-failure would leave the daemon silently dead.
    repo_list = load_repos()
    if not repo_list:
        log.error(
            "repos.conf not found or empty at %s — Mesy now requires a repos.conf "
            "with at least one [repo:*] section.",
            REPO_DIR / "repos.conf",
        )
        raise SystemExit(1)

    log.info("Loaded %d repo(s) from repos.conf", len(repo_list))
    try:
        repo_contexts = _build_repo_contexts(repo_list)
    except (ValueError, OSError) as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from None

    if not any(ctx.targets for ctx in repo_contexts):
        log.error("No targets configured in any sync.conf. Add [target:*] sections.")
        raise SystemExit(1)

    #refactor this. Each repo will have its own settings. Global default is built into load_config()
    # Global settings used as fallback defaults (e.g. scan_interval which is top-level).
    first_data_dir = repo_contexts[0].data_dir
    cfg = load_config(data_dir=first_data_dir)
    settings = get_mesy_settings(cfg)

    log.info("Mesy sync daemon starting (repo: %s)", REPO_DIR)
    for ctx in repo_contexts:
        log.info(
            "  [%s] data_dir=%s, targets=%s",
            ctx.name,
            ctx.data_dir,
            ", ".join(t.name for t in ctx.targets),
        )

    # Initial sync across all repos.
    for ctx in repo_contexts:
        if not _has_codex(ctx):
            _pull_context(ctx)
        if _scan_context(ctx):
            _commit_context(ctx)

    # Choose watch method.
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(repo_contexts, settings)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(repo_contexts, settings)
