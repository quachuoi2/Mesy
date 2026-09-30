"""One sync pass for a data repo: scan its targets, commit, pull."""

from __future__ import annotations

import logging
import subprocess

from src import git_sync
from src.factory import RepoCtx
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

_scan_failures: dict[tuple[str, str], str] = {}


def _scan_target(repo_ctx: RepoCtx, target: SyncTarget) -> bool:
    key = (repo_ctx.name, target.name)
    try:
        target.scan()
        if _scan_failures.pop(key, None):
            log.info("[%s/%s] Scan recovered", repo_ctx.name, target.name)
        return True

    except (OSError, ValueError, RuntimeError) as exc:
        # An unchanged failure repeats every scan; log it once until it changes.
        if _scan_failures.get(key) != str(exc):
            _scan_failures[key] = str(exc)
            log.error("[%s/%s] Scan deferred, blocking commits for this repo: %s",
                      repo_ctx.name, target.name, exc)
        return False

    except AssertionError:
        _scan_failures.pop(key, None)
        log.exception("[%s/%s] Scan hit an internal error", repo_ctx.name, target.name)
        return False


def pull_repo(repo_ctx: RepoCtx) -> None:
    try:
        git_sync.pull_remote(repo_base=repo_ctx.repo_base)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[%s] Pull deferred: %s", repo_ctx.name, exc)


def first_sync(repo_ctx: RepoCtx) -> None:
    pull_repo(repo_ctx)
    if scan_repo(repo_ctx):
        commit_repo(repo_ctx)

def scan_repo(repo_ctx: RepoCtx) -> bool:
    results = [_scan_target(repo_ctx, target) for target in repo_ctx.targets]
    return all(results)


def commit_repo(repo_ctx: RepoCtx) -> None:
    try:
        git_sync.commit_and_push(repo_ctx.stage_dirs, repo_base=repo_ctx.repo_base)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[%s] Git operation deferred: %s", repo_ctx.name, exc)


def run_intervals(repo_ctx: RepoCtx, now: float, last_pull: dict[str, float], last_scan: dict[str, float]) -> None:
    if (now - last_pull[repo_ctx.name]) > float(repo_ctx.settings["pull_interval_seconds"]):
        last_pull[repo_ctx.name] = now
        pull_repo(repo_ctx)
    if now - last_scan[repo_ctx.name] > float(repo_ctx.settings["scan_interval_seconds"]):
        last_scan[repo_ctx.name] = now
        scan_repo(repo_ctx)
