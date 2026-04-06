"""Configuration loading and target definitions for Mesy."""

from __future__ import annotations

import logging
from configparser import ConfigParser
from pathlib import Path

log = logging.getLogger("mesy")

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_DIR / "sync.conf"
PROJECTS_CONF_PATH = REPO_DIR / "projects.conf"


def load_config() -> ConfigParser:
    """Load sync.conf and return the full ConfigParser."""
    defaults = {
        "debounce_seconds": "30",
        "pull_interval_seconds": "60",
        "scan_interval_seconds": "60",
    }
    cfg = ConfigParser()
    cfg.read_dict({"mesy": defaults})
    if CONFIG_PATH.exists():
        cfg.read(CONFIG_PATH)
    return cfg


def get_mesy_settings(cfg: ConfigParser) -> dict:
    """Extract the [mesy] section as a dict."""
    return dict(cfg["mesy"])


def load_project_mappings() -> dict[str, str]:
    """Load projects.conf and return {raw_claude_name: canonical_name} mapping."""
    mapping = {}
    if not PROJECTS_CONF_PATH.exists():
        return mapping
    cfg = ConfigParser()
    cfg.read(PROJECTS_CONF_PATH)
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
    and optional: mapping_conf, file_patterns, exclude.
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

        targets.append(target_def)

    return targets
