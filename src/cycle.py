"""One sync pass for a data repo: scan its targets, commit, pull."""

from __future__ import annotations

import logging
import subprocess

from src.factory import RepoContext
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

_scan_failures: dict[tuple[str, str], str] = {}


def _scan_target(ctx: RepoContext, target: SyncTarget) -> bool:
    key = (ctx.name, target.name)
    try:
        target.scan()
        if _scan_failures.pop(key, None):
            log.info("[%s/%s] Scan recovered", ctx.name, target.name)
        return True

    except (OSError, ValueError, RuntimeError) as exc:
        # An unchanged failure repeats every scan; log it once until it changes.
        if _scan_failures.get(key) != str(exc):
            _scan_failures[key] = str(exc)
            log.error("[%s/%s] Scan deferred, blocking commits for this repo: %s",
                      ctx.name, target.name, exc)
        return False

    except AssertionError:
        _scan_failures.pop(key, None)
        log.exception("[%s/%s] Scan hit an internal error", ctx.name, target.name)
        return False


def pull_repo(ctx: RepoContext) -> None:
    if ctx.git_sync.isolated:
        # This also retries committed-but-unpushed work without new filesystem events.
        if scan_repo(ctx):
            commit_repo(ctx)
    else:
        _pull_alone(ctx)


def first_sync(ctx: RepoContext) -> None:
    _pull_alone(ctx)
    if scan_repo(ctx):
        commit_repo(ctx)


def _pull_alone(ctx: RepoContext) -> None:
    try:
        ctx.git_sync.pull(ctx.data_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[%s] Pull deferred: %s", ctx.name, exc)


def scan_repo(ctx: RepoContext) -> bool:
    results = [_scan_target(ctx, target) for target in ctx.targets]
    return all(results)


def commit_repo(ctx: RepoContext) -> None:
    try:
        if ctx.git_sync.push(ctx.data_dir, ctx.stage_dirs):
            scan_repo(ctx)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[%s] Git operation deferred: %s", ctx.name, exc)