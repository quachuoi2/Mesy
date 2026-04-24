"""Shared fixtures for the Mesy test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Create a temp Mesy repo structure and monkeypatch REPO_DIR/DATA_DIR everywhere."""
    repo = tmp_path / "Mesy"
    (repo / "claude" / "global").mkdir(parents=True)
    (repo / "claude" / "projects").mkdir(parents=True)
    (repo / "openclaw" / "main").mkdir(parents=True)

    # Patch in config (the single source of truth)
    monkeypatch.setattr("src.config.REPO_DIR", repo)
    monkeypatch.setattr("src.config.REPOS_CONF_PATH", repo / "repos.conf")

    # Propagate to all modules that import REPO_DIR directly
    monkeypatch.setattr("src.git_utils.REPO_DIR", repo)
    monkeypatch.setattr("src.main.REPO_DIR", repo)

    return repo


@pytest.fixture
def tmp_multi_repo(tmp_path, monkeypatch):
    """Create two independent data repos and patch REPOS_CONF_PATH.

    Returns a dict with keys:
      - repo_a: Path  (first data repo root)
      - repo_b: Path  (second data repo root)
      - repos_conf: Path  (the patched repos.conf file)
    """
    repo_a = tmp_path / "RepoA"
    repo_b = tmp_path / "RepoB"
    for repo in (repo_a, repo_b):
        (repo / "claude" / "global").mkdir(parents=True)
        (repo / "claude" / "projects").mkdir(parents=True)
        (repo / "openclaw" / "main").mkdir(parents=True)

    repos_conf = tmp_path / "repos.conf"
    repos_conf.write_text(
        "[repo:repo-a]\n"
        f"path = {repo_a}\n"
        "\n"
        "[repo:repo-b]\n"
        f"path = {repo_b}\n"
    )

    code_repo = tmp_path / "Mesy"
    code_repo.mkdir()
    monkeypatch.setattr("src.config.REPO_DIR", code_repo)
    monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)
    monkeypatch.setattr("src.main.REPO_DIR", code_repo)

    return {"repo_a": repo_a, "repo_b": repo_b, "repos_conf": repos_conf}


@pytest.fixture
def mock_git(monkeypatch):
    """Patch subprocess.run to capture git calls, returns a MagicMock with call recorder."""
    calls = []
    results: dict[tuple, subprocess.CompletedProcess] = {}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        key = tuple(cmd)
        if key in results:
            return results[key]
        # Build a generic successful result
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    mock = MagicMock()
    mock.calls = calls
    mock.results = results
    mock.set_result = lambda cmd_tuple, rc, stdout="", stderr="": results.__setitem__(
        cmd_tuple, subprocess.CompletedProcess(list(cmd_tuple), rc, stdout, stderr)
    )

    monkeypatch.setattr("subprocess.run", fake_run)
    return mock


@pytest.fixture
def tmp_source(tmp_path):
    """Create a temp source directory with sample .md files."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "notes.md").write_text("# Notes\nSome content.")
    (src / "todo.md").write_text("# Todo\n- item1\n- item2")
    (src / "ignore.txt").write_text("not a markdown file")
    return src
