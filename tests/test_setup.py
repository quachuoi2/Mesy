"""Tests for setup.py — repos.conf prompting and per-repo dispatch."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path
from unittest.mock import patch

import pytest

import setup


@pytest.fixture
def tmp_code_repo(tmp_path, monkeypatch):
    """Patch setup.REPO_DIR/REPOS_CONF_PATH and the config module paths to tmp_path."""
    code_repo = tmp_path / "Mesy"
    code_repo.mkdir()
    repos_conf = code_repo / "repos.conf"

    monkeypatch.setattr(setup, "REPO_DIR", code_repo)
    monkeypatch.setattr(setup, "REPOS_CONF_PATH", repos_conf)
    monkeypatch.setattr("src.config.REPO_DIR", code_repo)
    monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

    return {"code_repo": code_repo, "repos_conf": repos_conf}


def _feed_input(monkeypatch, answers: list[str]) -> None:
    """Queue a list of input() responses; raises if the code over-consumes."""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_prompt_repos_conf_appends_without_clobbering(tmp_code_repo, tmp_path, monkeypatch):
    """Adding a repo to an already-populated repos.conf preserves existing sections."""
    existing_path = tmp_path / "ExistingRepo"
    existing_path.mkdir()
    new_path = tmp_path / "NewRepo"
    new_path.mkdir()

    tmp_code_repo["repos_conf"].write_text(
        f"[repo:existing]\npath = {existing_path}\n"
    )

    _feed_input(
        monkeypatch,
        [
            "y",              # add another?
            "newbie",         # name
            str(new_path),    # path
            "",               # git remote (skip)
            "n",              # add another? (done)
        ],
    )

    repos = setup.prompt_repos_conf()

    assert [r["name"] for r in repos] == ["existing", "newbie"]

    cfg = ConfigParser()
    cfg.read(tmp_code_repo["repos_conf"])
    assert cfg.sections() == ["repo:existing", "repo:newbie"]
    assert cfg.get("repo:existing", "path") == str(existing_path)
    assert cfg.get("repo:newbie", "path") == str(new_path.resolve())


def test_prompt_repos_conf_rejects_path_inside_repo_dir(tmp_code_repo, tmp_path, monkeypatch, capsys):
    """A path resolving inside REPO_DIR is rejected; the resolved path is printed."""
    inside = tmp_code_repo["code_repo"] / "sneaky"
    inside.mkdir()
    outside = tmp_path / "Outside"
    outside.mkdir()

    _feed_input(
        monkeypatch,
        [
            "repo1",            # name
            str(inside),        # bad path (inside REPO_DIR)
            str(outside),       # retry with valid path
            "",                 # git remote (skip)
            "n",                # add another? done
        ],
    )

    repos = setup.prompt_repos_conf()

    out = capsys.readouterr().out
    assert str(inside.resolve()) in out
    assert "inside the Mesy code dir" in out
    assert repos[0]["path"] == outside.resolve()


def test_prompt_repos_conf_rejects_symlink_that_resolves_inside(tmp_code_repo, tmp_path, monkeypatch, capsys):
    """A symlink that resolves into REPO_DIR is rejected (resolve-then-check)."""
    real_inside = tmp_code_repo["code_repo"] / "data"
    real_inside.mkdir()
    link = tmp_path / "external_link"
    link.symlink_to(real_inside)

    outside = tmp_path / "GoodRepo"
    outside.mkdir()

    _feed_input(
        monkeypatch,
        [
            "r1",               # name
            str(link),          # symlink resolving inside
            str(outside),       # valid path
            "",                 # git remote (skip)
            "n",
        ],
    )

    setup.prompt_repos_conf()

    out = capsys.readouterr().out
    assert str(real_inside.resolve()) in out


def test_main_invokes_setup_repo_per_repo(tmp_code_repo, tmp_path, monkeypatch):
    """main() calls setup_repo once per repo returned from prompt_repos_conf."""
    repo_a = tmp_path / "A"
    repo_b = tmp_path / "B"
    for r in (repo_a, repo_b):
        r.mkdir()
        (r / "sync.conf").write_text("[mesy]\n")

    monkeypatch.setattr(
        setup,
        "prompt_repos_conf",
        lambda: [{"name": "a", "path": repo_a}, {"name": "b", "path": repo_b}],
    )

    calls = []
    monkeypatch.setattr(
        setup,
        "setup_repo",
        lambda data_dir, seen: calls.append(data_dir),
    )
    monkeypatch.setattr(setup, "install_daemon", lambda: None)

    setup.main()

    assert calls == [repo_a, repo_b]


def test_ensure_sync_conf_offers_template_when_missing(tmp_path, monkeypatch, capsys):
    """When sync.conf is missing, user can opt into a template; file is written."""
    data_dir = tmp_path / "NewRepo"
    data_dir.mkdir()

    _feed_input(monkeypatch, ["y"])

    assert setup.ensure_sync_conf(data_dir) is True
    assert (data_dir / "sync.conf").exists()
    assert "[mesy]" in (data_dir / "sync.conf").read_text()


def test_ensure_sync_conf_skips_when_declined(tmp_path, monkeypatch):
    """When user declines the template, the repo is skipped."""
    data_dir = tmp_path / "EmptyRepo"
    data_dir.mkdir()

    _feed_input(monkeypatch, ["n"])

    assert setup.ensure_sync_conf(data_dir) is False
    assert not (data_dir / "sync.conf").exists()


def test_setup_git_sync_clones_when_dir_empty(tmp_path, monkeypatch):
    """Empty target dir + remote URL → git clone is invoked."""
    data_dir = tmp_path / "Empty"
    data_dir.mkdir()

    _feed_input(monkeypatch, ["git@github.com:me/r.git"])

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # Recreate the dir so the test's assertions hold.
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    setup._setup_git_sync(data_dir)

    assert calls[0][:2] == ["git", "clone"]
    assert "git@github.com:me/r.git" in calls[0]


def test_setup_git_sync_inits_when_dir_nonempty(tmp_path, monkeypatch):
    """Non-empty dir + remote URL → init + remote add + fetch + pull."""
    data_dir = tmp_path / "Existing"
    data_dir.mkdir()
    (data_dir / "stale.md").write_text("hi")

    _feed_input(monkeypatch, ["https://example.com/r.git"])

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "refs/remotes/origin/main", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    setup._setup_git_sync(data_dir)

    invoked = [tuple(c[3:]) for c in calls]  # strip ["git","-C",dir]
    assert ("init",) in invoked
    assert ("remote", "add", "origin", "https://example.com/r.git") in invoked
    assert ("fetch", "origin") in invoked


def test_setup_git_sync_skips_existing_repo(tmp_path, monkeypatch):
    """If `.git` already exists, no prompt is issued and no commands run."""
    data_dir = tmp_path / "AlreadyRepo"
    (data_dir / ".git").mkdir(parents=True)

    # No input expected — _feed_input with empty iter would raise on consume.
    _feed_input(monkeypatch, [])

    called = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(a) or None)

    setup._setup_git_sync(data_dir)

    assert called == []


def test_setup_git_sync_blank_url_skips(tmp_path, monkeypatch):
    """Blank URL → warn and do nothing destructive."""
    data_dir = tmp_path / "Blank"
    data_dir.mkdir()

    _feed_input(monkeypatch, [""])

    called = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(a) or None)

    setup._setup_git_sync(data_dir)

    assert called == []
    assert data_dir.exists()


def test_setup_repo_skips_duplicate_target_types(tmp_path, monkeypatch, capsys):
    """A second repo declaring the same target type is warned and skipped."""
    data_dir = tmp_path / "Repo"
    data_dir.mkdir()
    (data_dir / "sync.conf").write_text(
        "[mesy]\n"
        "[target:claude_code]\n"
        "source = ~/.claude\n"
        "repo_subdir = claude\n"
        "mapping_conf = claude/projects.conf\n"
    )

    calls = []
    monkeypatch.setattr(setup, "setup_claude_code_symlinks", lambda d: calls.append(d))

    # First pass: type is claimed.
    seen: set[str] = set()
    setup.setup_repo(data_dir, seen)
    assert calls == [data_dir]
    assert "claude-code" in seen

    # Second pass: same type already seen → skip, no additional call.
    setup.setup_repo(data_dir, seen)
    assert calls == [data_dir]
    out = capsys.readouterr().out
    assert "already handled by an earlier repo" in out


def test_make_symlink_keeps_subfolders(tmp_path):
    link = tmp_path / "memory"
    (link / "sub").mkdir(parents=True)
    (link / "top.md").write_text("top")
    (link / "sub" / "deep.md").write_text("deep")
    target = tmp_path / "repo" / "memory"

    setup.make_symlink(link, target)

    assert link.is_symlink()
    assert (target / "top.md").read_text() == "top"
    assert (target / "sub" / "deep.md").read_text() == "deep"


def test_make_symlink_keeps_folder_when_copy_incomplete(tmp_path, monkeypatch):
    link = tmp_path / "memory"
    link.mkdir()
    (link / "notes.md").write_text("notes")
    monkeypatch.setattr(setup, "all_copied", lambda src, dst: False)

    setup.make_symlink(link, tmp_path / "repo" / "memory")

    assert not link.is_symlink()
    assert (link / "notes.md").read_text() == "notes"
