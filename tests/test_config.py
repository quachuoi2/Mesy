"""Tests for mesy/config.py — config loading and target parsing."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import pytest

import src.config as config_mod
from src.config import (
    get_mesy_settings,
    load_config,
    load_project_mappings,
    load_targets,
)


class TestLoadConfig:
    def test_defaults_when_conf_missing(self, tmp_repo, monkeypatch):
        """load_config() returns defaults when sync.conf does not exist."""
        # tmp_repo already patches CONFIG_PATH to a non-existent path
        cfg = load_config()
        settings = get_mesy_settings(cfg)
        assert settings["debounce_seconds"] == "30"
        assert settings["pull_interval_seconds"] == "60"
        assert settings["scan_interval_seconds"] == "60"

    def test_custom_mesy_settings(self, tmp_repo, monkeypatch):
        """Custom [mesy] values override defaults."""
        conf = tmp_repo / "sync.conf"
        conf.write_text("[mesy]\ndebounce_seconds = 10\npull_interval_seconds = 120\n")
        cfg = load_config()
        settings = get_mesy_settings(cfg)
        assert settings["debounce_seconds"] == "10"
        assert settings["pull_interval_seconds"] == "120"
        # un-overridden key keeps its default
        assert settings["scan_interval_seconds"] == "60"

    def test_parse_claude_code_target(self, tmp_repo):
        """[target:*] with mapping_conf produces type=claude-code."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:claude-code]\n"
            "source = /tmp/projects\n"
            "repo_subdir = projects\n"
            "mapping_conf = projects.conf\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert len(targets) == 1
        assert targets[0]["type"] == "claude-code"
        assert targets[0]["name"] == "claude-code"
        assert targets[0]["mapping_conf"] == "projects.conf"

    def test_parse_generic_target(self, tmp_repo):
        """[target:*] without mapping_conf produces type=generic."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert len(targets) == 1
        assert targets[0]["type"] == "generic"
        assert targets[0]["name"] == "openclaw"

    def test_parse_file_patterns(self, tmp_repo):
        """file_patterns list is parsed from comma-separated config value."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "file_patterns = *.md, *.txt\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["file_patterns"] == ["*.md", "*.txt"]

    def test_default_file_patterns(self, tmp_repo):
        """file_patterns defaults to ['*.md'] when not specified."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["file_patterns"] == ["*.md"]

    def test_parse_exclude_list(self, tmp_repo):
        """exclude list is parsed from comma-separated config value."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "exclude = TOOLS.md, HEARTBEAT.md\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["exclude"] == ["TOOLS.md", "HEARTBEAT.md"]


class TestLoadProjectMappings:
    def test_load_project_mappings(self, tmp_repo):
        """Mappings from projects.conf are parsed correctly."""
        conf = tmp_repo / "projects.conf"
        conf.write_text(
            "[Workspace]\n"
            "paths = -home-user-Workspace, -home-user2-Workspace\n"
        )
        mappings = load_project_mappings()
        assert mappings["-home-user-Workspace"] == "Workspace"
        assert mappings["-home-user2-Workspace"] == "Workspace"

    def test_missing_projects_conf(self, tmp_repo):
        """load_project_mappings() returns empty dict when projects.conf missing."""
        mappings = load_project_mappings()
        assert mappings == {}
