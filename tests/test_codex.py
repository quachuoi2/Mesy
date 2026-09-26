"""Tests for the Codex target: AGENTS.md and memories/ via the shared base sync."""

from __future__ import annotations

import pytest

from src.targets.codex import CodexTarget


@pytest.fixture
def env(tmp_path):
    codex_home = tmp_path / "codex_home"
    data_dir = tmp_path / "data"
    codex_home.mkdir()
    data_dir.mkdir()
    return codex_home, data_dir


def _target(env, sync_mode="symlink"):
    codex_home, data_dir = env
    return CodexTarget(name="codex", source=codex_home, repo_dir=data_dir / "codex",
                       data_dir=data_dir, sync_mode=sync_mode)


def test_adopts_agents_and_nested_memories(env):
    codex_home, data_dir = env
    (codex_home / "AGENTS.md").write_text("rules")
    (codex_home / "memories" / "notes").mkdir(parents=True)
    (codex_home / "memories" / "notes" / "a.md").write_text("deep")

    _target(env).scan()

    assert (codex_home / "AGENTS.md").is_symlink()
    assert (data_dir / "codex" / "AGENTS.md").read_text() == "rules"
    assert (codex_home / "memories").is_symlink()
    assert (data_dir / "codex" / "memories" / "notes" / "a.md").read_text() == "deep"


def test_links_repo_files_on_new_machine(env):
    codex_home, data_dir = env
    (data_dir / "codex" / "memories").mkdir(parents=True)
    (data_dir / "codex" / "AGENTS.md").write_text("rules")

    _target(env).scan()

    assert (codex_home / "AGENTS.md").read_text() == "rules"
    assert (codex_home / "memories").resolve() == (data_dir / "codex" / "memories").resolve()


def test_copy_mode_keeps_real_files(env):
    codex_home, data_dir = env
    (codex_home / "memories" / "sub").mkdir(parents=True)
    (codex_home / "memories" / "sub" / "a.md").write_text("local")
    (data_dir / "codex" / "memories").mkdir(parents=True)
    (data_dir / "codex" / "memories" / "b.md").write_text("remote")

    _target(env, sync_mode="copy").scan()

    assert not (codex_home / "memories").is_symlink()
    assert (data_dir / "codex" / "memories" / "sub" / "a.md").read_text() == "local"
    assert (codex_home / "memories" / "b.md").read_text() == "remote"


def test_stage_dir_is_repo_subdir(env):
    assert _target(env).get_stage_dirs() == ["codex/"]
