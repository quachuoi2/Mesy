"""Configuration loading and target definitions for Mesy."""

from __future__ import annotations

import logging
import os
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

REPO_DIR = Path(__file__).resolve().parent.parent
REPOS_CONF_PATH = REPO_DIR / "repos.conf"


def _resolve_data_dir() -> Path:
    """Resolve DATA_DIR in priority order:
    1. MESY_DATA_DIR env var (if set and points to a valid directory)
    2. Sibling ../Mesy-Data relative to REPO_DIR
    3. REPO_DIR (backwards compat fallback)
    """
    env_val = os.environ.get("MESY_DATA_DIR", "").strip()
    if env_val:
        candidate = Path(env_val).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        log.warning("MESY_DATA_DIR=%s is not a valid directory, ignoring", env_val)

    sibling = (REPO_DIR / ".." / "Mesy-Data").resolve()
    if sibling.is_dir():
        return sibling

    return REPO_DIR


DATA_DIR = _resolve_data_dir()
CONFIG_PATH = DATA_DIR / "sync.conf"
PROJECTS_CONF_PATH = DATA_DIR / "claude" / "projects.conf"


@dataclass
class RepoContext:
    """Bundles everything the daemon needs to manage one data repo."""

    name: str
    data_dir: Path
    targets: list[SyncTarget] = field(default_factory=list)
    stage_dirs: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def load_repos() -> list[dict]:
    """Parse repos.conf and return a list of repo dicts.

    Each dict has keys: name (str), path (Path).

    Returns an empty list if repos.conf does not exist (caller should fall
    back to single-repo mode using DATA_DIR).
    """
    if not REPOS_CONF_PATH.exists():
        return []

    cfg = ConfigParser()
    cfg.read(REPOS_CONF_PATH)

    repos = []
    for section in cfg.sections():
        if not section.startswith("repo:"):
            continue
        name = section.split(":", 1)[1].strip()
        raw_path = cfg.get(section, "path", fallback="").strip()
        if not raw_path:
            log.warning("[repos.conf] Section [%s] has no 'path' key, skipping", section)
            continue
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            log.warning(
                "[repos.conf] Repo '%s' path=%s is not a valid directory, skipping",
                name,
                path,
            )
            continue
        repos.append({"name": name, "path": path})

    return repos


def load_config(data_dir: Path | None = None) -> ConfigParser:
    """Load sync.conf from data_dir and return the full ConfigParser.

    Falls back to the module-level CONFIG_PATH (DATA_DIR/sync.conf) when
    data_dir is not provided, preserving backward compatibility.
    """
    config_path = (data_dir / "sync.conf") if data_dir is not None else CONFIG_PATH
    defaults = {
        "debounce_seconds": "30",
        "pull_interval_seconds": "60",
        "scan_interval_seconds": "60",
    }
    cfg = ConfigParser()
    cfg.read_dict({"mesy": defaults})
    if config_path.exists():
        cfg.read(config_path)
    return cfg


def get_mesy_settings(cfg: ConfigParser) -> dict:
    """Extract the [mesy] section as a dict."""
    return dict(cfg["mesy"])


def load_project_mappings(data_dir: Path | None = None) -> dict[str, str]:
    """Load projects.conf and return {raw_claude_name: canonical_name} mapping.

    Uses data_dir / claude / projects.conf when data_dir is provided, otherwise
    falls back to the module-level PROJECTS_CONF_PATH.
    """
    projects_conf = (
        data_dir / "claude" / "projects.conf" if data_dir is not None else PROJECTS_CONF_PATH
    )
    mapping = {}
    if not projects_conf.exists():
        return mapping
    cfg = ConfigParser()
    cfg.read(projects_conf)
    for section in cfg.sections():
        if section == "mesy":
            continue
        canonical = section
        paths_str = cfg.get(section, "paths", fallback="")
        for raw_name in paths_str.split(","):
            raw_name = raw_name.strip()
            if raw_name:
                mapping[raw_name] = canonical
    return mapping


def load_targets(cfg: ConfigParser) -> list[dict]:
    """Parse [target:*] sections from config into target definitions.

    Returns a list of dicts with keys: name, type, source, repo_subdir,
    and optional: mapping_conf, file_patterns, exclude, sync_mode.
    """
    targets = []
    for section in cfg.sections():
        if not section.startswith("target:"):
            continue
        name = section.split(":", 1)[1]
        target_def = {"name": name}

        target_def["source"] = Path(cfg.get(section, "source")).expanduser()
        target_def["repo_subdir"] = cfg.get(section, "repo_subdir")

        if cfg.has_option(section, "mapping_conf"):
            target_def["type"] = "claude-code"
            target_def["mapping_conf"] = cfg.get(section, "mapping_conf")
        else:
            target_def["type"] = "generic"

        if cfg.has_option(section, "file_patterns"):
            target_def["file_patterns"] = [
                p.strip() for p in cfg.get(section, "file_patterns").split(",")
            ]
        else:
            target_def["file_patterns"] = ["*.md"]

        if cfg.has_option(section, "exclude"):
            target_def["exclude"] = [
                e.strip() for e in cfg.get(section, "exclude").split(",")
            ]
        else:
            target_def["exclude"] = []

        raw_sync_mode = cfg.get(section, "sync_mode", fallback="symlink").strip()
        valid_sync_modes = {"symlink", "copy"}
        if raw_sync_mode not in valid_sync_modes:
            log.warning(
                "[target:%s] Invalid sync_mode=%r; must be one of %s — defaulting to 'symlink'",
                name,
                raw_sync_mode,
                sorted(valid_sync_modes),
            )
            raw_sync_mode = "symlink"
        target_def["sync_mode"] = raw_sync_mode

        targets.append(target_def)

    return targets
