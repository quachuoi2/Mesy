"""Tests for setup.py — repos.ini prompting and per-repo dispatch."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path
from unittest.mock import patch

import pytest

import setup


@pytest.fixture
def tmp_code_repo(tmp_path, monkeypatch):
    """Patch setup.MESY_DIR/REPOS_CFG_PATH and the config module paths to tmp_path."""
    code_repo = tmp_path / "Mesy"
    code_repo.mkdir()
    repos_cfg = code_repo / "repos.ini"

    monkeypatch.setattr(setup, "MESY_DIR", code_repo)
    monkeypatch.setattr(setup, "REPOS_CFG_PATH", repos_cfg)
    monkeypatch.setattr("src.config.MESY_DIR", code_repo)
    monkeypatch.setattr("src.config.REPOS_CFG_PATH", repos_cfg)

    return {"code_repo": code_repo, "repos_cfg": repos_cfg}


def _feed_input(monkeypatch, answers: list[str]) -> None:
    """Queue a list of input() responses; raises if the code over-consumes."""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_prompt_repos_cfg_appends_without_clobbering(tmp_code_repo, tmp_path, monkeypatch):
    """Adding a repo to an already-populated repos.ini preserves existing sections."""
    existing_path = tmp_path / "ExistingRepo"
    existing_path.mkdir()
    new_path = tmp_path / "NewRepo"
    new_path.mkdir()

    tmp_code_repo["repos_cfg"].write_text(
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

    repos = setup.prompt_repos_cfg()

    assert [r["name"] for r in repos] == ["existing", "newbie"]

    repos_cfg = ConfigParser()
    repos_cfg.read(tmp_code_repo["repos_cfg"])
    assert repos_cfg.sections() == ["repo:existing", "repo:newbie"]
    assert repos_cfg.get("repo:existing", "path") == str(existing_path)
    assert repos_cfg.get("repo:newbie", "path") == str(new_path.resolve())


def test_prompt_repos_cfg_rejects_path_inside_mesy_dir(tmp_code_repo, tmp_path, monkeypatch, capsys):
    """A path resolving inside MESY_DIR is rejected; the resolved path is printed."""
    inside = tmp_code_repo["code_repo"] / "sneaky"
    inside.mkdir()
    outside = tmp_path / "Outside"
    outside.mkdir()

    _feed_input(
        monkeypatch,
        [
            "repo1",            # name
            str(inside),        # bad path (inside MESY_DIR)
            str(outside),       # retry with valid path
            "",                 # git remote (skip)
            "n",                # add another? done
        ],
    )

    repos = setup.prompt_repos_cfg()

    out = capsys.readouterr().out
    assert str(inside.resolve()) in out
    assert "inside the Mesy code dir" in out
    assert repos[0]["path"] == outside.resolve()


def test_prompt_repos_cfg_rejects_symlink_that_resolves_inside(tmp_code_repo, tmp_path, monkeypatch, capsys):
    """A symlink that resolves into MESY_DIR is rejected (resolve-then-check)."""
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

    setup.prompt_repos_cfg()

    out = capsys.readouterr().out
    assert str(real_inside.resolve()) in out


def test_main_invokes_setup_repo_per_repo(tmp_code_repo, tmp_path, monkeypatch):
    """main() calls setup_repo once per repo returned from prompt_repos_cfg."""
    repo_a = tmp_path / "A"
    repo_b = tmp_path / "B"
    for r in (repo_a, repo_b):
        r.mkdir()
        (r / "sync.ini").write_text("[mesy]\n")

    monkeypatch.setattr(
        setup,
        "prompt_repos_cfg",
        lambda: [{"name": "a", "path": repo_a}, {"name": "b", "path": repo_b}],
    )

    calls = []
    monkeypatch.setattr(
        setup,
        "setup_repo",
        lambda repo_base, seen: calls.append(repo_base),
    )
    monkeypatch.setattr(setup, "install_daemon", lambda: None)

    setup.main()

    assert calls == [repo_a, repo_b]


def test_ensure_sync_cfg_offers_template_when_missing(tmp_path, monkeypatch, capsys):
    """When sync.ini is missing, user can opt into a template; file is written."""
    repo_base = tmp_path / "NewRepo"
    repo_base.mkdir()

    _feed_input(monkeypatch, ["y"])

    assert setup.ensure_sync_cfg(repo_base) is True
    assert (repo_base / "sync.ini").exists()
    assert "[mesy]" in (repo_base / "sync.ini").read_text()


def test_ensure_sync_cfg_skips_when_declined(tmp_path, monkeypatch):
    """When user declines the template, the repo is skipped."""
    repo_base = tmp_path / "EmptyRepo"
    repo_base.mkdir()

    _feed_input(monkeypatch, ["n"])

    assert setup.ensure_sync_cfg(repo_base) is False
    assert not (repo_base / "sync.ini").exists()


def test_setup_git_sync_clones_when_dir_empty(tmp_path, monkeypatch):
    """Empty target dir + remote URL → git clone is invoked."""
    repo_base = tmp_path / "Empty"
    repo_base.mkdir()

    _feed_input(monkeypatch, ["git@github.com:me/r.git"])

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # Recreate the dir so the test's assertions hold.
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    setup._setup_git_sync(repo_base)

    assert calls[0][:2] == ["git", "clone"]
    assert "git@github.com:me/r.git" in calls[0]


def test_setup_git_sync_inits_when_dir_nonempty(tmp_path, monkeypatch):
    """Non-empty dir + remote URL → init + remote add + fetch + pull."""
    repo_base = tmp_path / "Existing"
    repo_base.mkdir()
    (repo_base / "stale.md").write_text("hi")

    _feed_input(monkeypatch, ["https://example.com/r.git"])

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "refs/remotes/origin/main", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    setup._setup_git_sync(repo_base)

    invoked = [tuple(c[3:]) for c in calls]  # strip ["git","-C",dir]
    assert ("init",) in invoked
    assert ("remote", "add", "origin", "https://example.com/r.git") in invoked
    assert ("fetch", "origin") in invoked


def test_setup_git_sync_skips_existing_repo(tmp_path, monkeypatch):
    """If `.git` already exists, no prompt is issued and no commands run."""
    repo_base = tmp_path / "AlreadyRepo"
    (repo_base / ".git").mkdir(parents=True)

    # No input expected — _feed_input with empty iter would raise on consume.
    _feed_input(monkeypatch, [])

    called = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(a) or None)

    setup._setup_git_sync(repo_base)

    assert called == []


def test_setup_git_sync_blank_url_skips(tmp_path, monkeypatch):
    """Blank URL → warn and do nothing destructive."""
    repo_base = tmp_path / "Blank"
    repo_base.mkdir()

    _feed_input(monkeypatch, [""])

    called = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.append(a) or None)

    setup._setup_git_sync(repo_base)

    assert called == []
    assert repo_base.exists()


def test_setup_repo_skips_duplicate_target_types(tmp_path, monkeypatch, capsys):
    """A second repo declaring the same target type is warned and skipped."""
    repo_base = tmp_path / "Repo"
    repo_base.mkdir()
    (repo_base / "sync.ini").write_text(
        "[mesy]\n"
        "[target:claude-code]\n"
        "source = ~/.claude\n"
        "repo_subdir = claude\n"
    )

    calls = []
    monkeypatch.setattr(setup, "setup_claude_code_symlinks", lambda d: calls.append(d))

    seen: set[str] = set()
    setup.setup_repo(repo_base, seen)
    assert calls == [repo_base]
    assert "claude-code" in seen

    setup.setup_repo(repo_base, seen)
    assert calls == [repo_base]
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


def test_setup_claude_code_symlinks_links_rules(tmp_path, monkeypatch):
    claude_dir = tmp_path / ".claude"
    (claude_dir / "rules").mkdir(parents=True)
    (claude_dir / "rules" / "code.md").write_text("# Code")
    monkeypatch.setattr(setup, "CLAUDE_DIR", claude_dir)
    repo_base = tmp_path / "AlfredMemory"

    setup.setup_claude_code_symlinks(repo_base)

    repo_rules = repo_base / "claude" / "global" / "rules"
    assert (claude_dir / "rules").is_symlink()
    assert (repo_rules / "code.md").read_text() == "# Code"


def test_make_symlink_keeps_folder_when_copy_incomplete(tmp_path, monkeypatch):
    link = tmp_path / "memory"
    link.mkdir()
    (link / "notes.md").write_text("notes")
    monkeypatch.setattr(setup, "all_copied", lambda src, dest_dir: False)

    setup.make_symlink(link, tmp_path / "repo" / "memory")

    assert not link.is_symlink()
    assert (link / "notes.md").read_text() == "notes"


def test_link_repo_item_copies_when_windows_symlink_fails(tmp_path, monkeypatch):
    """Windows without symlink permission: a repo-only file is copied instead of crashing setup."""
    src = tmp_path / "src"
    src.mkdir()
    repo_file = tmp_path / "repo" / "notes.md"
    repo_file.parent.mkdir()
    repo_file.write_text("# Notes")

    def no_symlinks(*args, **kwargs):
        raise OSError("symlink not permitted")

    monkeypatch.setattr(setup, "IS_WINDOWS", True)
    monkeypatch.setattr(setup.os, "symlink", no_symlinks)

    setup._link_repo_item(repo_file, src, "demo", ["*.md"], [])

    assert (src / "notes.md").read_text() == "# Notes"
    assert not (src / "notes.md").is_symlink()
