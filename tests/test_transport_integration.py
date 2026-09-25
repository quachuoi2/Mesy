"""Exercise the transport against real local Git repositories.

Complements test_transport.py, which mocks run_git to assert command decisions.
These tests run actual merges, commits, and pushes between throwaway local
repositories. Nothing here touches a network remote or a real memory home.
"""

import subprocess

import pytest

from src.git_sync.isolated import sync_repository


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def commit_all(root, message):
    git(root, "add", "-A")
    git(root, "commit", "-m", message)


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(path))
    # Match the branch the clones create, so the first push has somewhere to land.
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    return path


def clone(remote, path, name):
    git(path.parent, "clone", "-q", str(remote), str(path))
    git(path, "config", "user.email", f"{name}@test")
    git(path, "config", "user.name", name)
    git(path, "checkout", "-q", "-B", "main")
    return path


@pytest.fixture
def working(tmp_path, remote):
    root = clone(remote, tmp_path / "work", "work")
    (root / "codex" / "global").mkdir(parents=True)
    (root / "codex" / "global" / "AGENTS.md").write_text("base\n")
    commit_all(root, "base")
    git(root, "push", "-q", "-u", "origin", "main")
    return root


@pytest.fixture
def peer(tmp_path, remote, working):
    return clone(remote, tmp_path / "peer", "peer")


def test_commits_and_pushes_managed_change(working, remote):
    (working / "codex" / "global" / "AGENTS.md").write_text("local edit\n")

    assert sync_repository(working, ["codex"]) is True

    assert "local edit" in git(remote, "show", "main:codex/global/AGENTS.md").stdout


def test_clean_merge_of_disjoint_remote_change(working, peer, remote):
    (peer / "codex" / "global").mkdir(parents=True, exist_ok=True)
    (peer / "codex" / "global" / "OTHER.md").write_text("peer note\n")
    commit_all(peer, "peer note")
    git(peer, "push", "-q", "origin", "main")

    (working / "codex" / "global" / "AGENTS.md").write_text("local edit\n")
    assert sync_repository(working, ["codex"]) is True

    # Both sides survive the merge, and the result reaches the remote.
    assert (working / "codex" / "global" / "OTHER.md").read_text() == "peer note\n"
    assert (working / "codex" / "global" / "AGENTS.md").read_text() == "local edit\n"
    assert "peer note" in git(remote, "show", "main:codex/global/OTHER.md").stdout
    assert "local edit" in git(remote, "show", "main:codex/global/AGENTS.md").stdout


def test_conflict_preserves_live_file_and_saves_versions(working, peer):
    (peer / "codex" / "global" / "AGENTS.md").write_text("peer version\n")
    commit_all(peer, "peer edit")
    git(peer, "push", "-q", "origin", "main")

    (working / "codex" / "global" / "AGENTS.md").write_text("local version\n")
    assert sync_repository(working, ["codex"]) is False

    live = working / "codex" / "global" / "AGENTS.md"
    assert live.read_text() == "local version\n"
    assert "<<<<<<<" not in live.read_text()

    saved = list((working / ".mesy-local" / "git-conflicts").iterdir())
    assert len(saved) == 1
    versions = {p.name for p in saved[0].iterdir()}
    assert {"local", "remote"} <= versions


def test_remote_deletion_propagates_on_merge(working, peer):
    (working / "codex" / "global" / "DOOMED.md").write_text("temporary\n")
    commit_all(working, "add doomed")
    git(working, "push", "-q", "origin", "main")
    git(peer, "pull", "-q", "--rebase", "origin", "main")

    (peer / "codex" / "global" / "DOOMED.md").unlink()
    commit_all(peer, "remove doomed")
    git(peer, "push", "-q", "origin", "main")

    (working / "codex" / "global" / "AGENTS.md").write_text("local edit\n")
    assert sync_repository(working, ["codex"]) is True

    assert not (working / "codex" / "global" / "DOOMED.md").exists()


def test_unrelated_tracked_modification_defers_without_losing_work(working):
    (working / "unrelated.txt").write_text("tracked\n")
    commit_all(working, "add unrelated")
    (working / "unrelated.txt").write_text("edited outside mesy\n")
    (working / "codex" / "global" / "AGENTS.md").write_text("local edit\n")

    assert sync_repository(working, ["codex"]) is False

    # Managed content is committed; the unrelated edit stays in the working tree.
    assert git(working, "status", "--porcelain").stdout.strip() == "M unrelated.txt"
    assert (working / "unrelated.txt").read_text() == "edited outside mesy\n"
    assert "local edit" in git(working, "show", "HEAD:codex/global/AGENTS.md").stdout


def test_push_failure_retries_on_next_call(working, remote, tmp_path):
    (working / "codex" / "global" / "AGENTS.md").write_text("offline edit\n")
    git(working, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    assert sync_repository(working, ["codex"]) is False
    assert "offline edit" in git(working, "show", "HEAD:codex/global/AGENTS.md").stdout

    git(working, "remote", "set-url", "origin", str(remote))
    assert sync_repository(working, ["codex"]) is True
    assert "offline edit" in git(remote, "show", "main:codex/global/AGENTS.md").stdout


def test_non_main_branch_is_used_for_push(working, remote):
    git(working, "checkout", "-q", "-b", "memory-sync")
    (working / "codex" / "global" / "AGENTS.md").write_text("branch edit\n")

    assert sync_repository(working, ["codex"]) is True

    assert "branch edit" in git(remote, "show", "memory-sync:codex/global/AGENTS.md").stdout
