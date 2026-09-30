"""Shared fixtures for the Mesy test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Create a temp Mesy repo structure and monkeypatch MESY_DIR/REPOS_CFG_PATH."""
    repo = tmp_path / "Mesy"
    (repo / "claude" / "global").mkdir(parents=True)
    (repo / "claude" / "projects").mkdir(parents=True)
    (repo / "openclaw" / "main").mkdir(parents=True)

    monkeypatch.setattr("src.config.MESY_DIR", repo)
    monkeypatch.setattr("src.config.REPOS_CFG_PATH", repo / "repos.ini")

    # Propagate to all modules that import MESY_DIR directly
    monkeypatch.setattr("src.main.MESY_DIR", repo)

    return repo


@pytest.fixture
def tmp_multi_repo(tmp_path, monkeypatch):
    """Create two independent data repos and patch REPOS_CFG_PATH.

    Returns a dict with keys:
      - repo_a: Path  (first data repo root)
      - repo_b: Path  (second data repo root)
      - repos_cfg: Path  (the patched repos.ini file)
    """
    repo_a = tmp_path / "RepoA"
    repo_b = tmp_path / "RepoB"
    for repo in (repo_a, repo_b):
        (repo / "claude" / "global").mkdir(parents=True)
        (repo / "claude" / "projects").mkdir(parents=True)
        (repo / "openclaw" / "main").mkdir(parents=True)

    repos_cfg = tmp_path / "repos.ini"
    repos_cfg.write_text(
        "[repo:repo-a]\n"
        f"path = {repo_a}\n"
        "\n"
        "[repo:repo-b]\n"
        f"path = {repo_b}\n"
    )

    code_repo = tmp_path / "Mesy"
    code_repo.mkdir()

    monkeypatch.setattr("src.config.MESY_DIR", code_repo)
    monkeypatch.setattr("src.config.REPOS_CFG_PATH", repos_cfg)
    monkeypatch.setattr("src.main.MESY_DIR", code_repo)

    return {"repo_a": repo_a, "repo_b": repo_b, "repos_cfg": repos_cfg}


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
def tmp_src(tmp_path):
    """Create a temp source directory with sample .md files."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "notes.md").write_text("# Notes\nSome content.")
    (src / "todo.md").write_text("# Todo\n- item1\n- item2")
    (src / "ignore.txt").write_text("not a markdown file")
    return src
