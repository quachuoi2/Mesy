"""Exercise Git command decisions with mocks; never commit or push real repos."""

import subprocess
from pathlib import Path

import pytest

from src.transport import sync_repository


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "codex" / "global").mkdir(parents=True)
    (tmp_path / "codex" / "global" / "AGENTS.md").write_text("Instructions")
    return tmp_path


class GitScenario:
    def __init__(self, root, *, changed=False, dirty=False, divergent=False,
                 conflict=False, offline=False, race=False, remote_symlink=False):
        self.root = root
        self.changed = changed
        self.dirty = dirty
        self.divergent = divergent
        self.conflict = conflict
        self.offline = offline
        self.race = race
        self.remote_symlink = remote_symlink
        self.calls = []
        self.head_reads = 0

    def __call__(self, data_dir, *args, binary=False):
        self.calls.append((data_dir, args))
        code, output, error = 0, "", ""
        if args[0] == "symbolic-ref":
            output = "main\n"
        elif args[0] == "status":
            output = " M codex/global/AGENTS.md\n" if ("--" in args and self.changed) else ""
            if "--untracked-files=no" in args and self.dirty:
                output = "M  unrelated.txt\n"
        elif args[0] == "diff" and "--quiet" in args:
            code = int(self.changed)
        elif args[0] == "fetch" and self.offline:
            code, error = 128, "offline"
        elif args == ("rev-parse", "HEAD"):
            if data_dir == self.root:
                self.head_reads += 1
                output = "changed-head" if self.race and self.head_reads > 1 else "local-head"
            else:
                output = "merged-head"
        elif args == ("rev-parse", "FETCH_HEAD"):
            output = "remote-head"
        elif args[0] == "merge-base":
            code = int(self.divergent)
        elif args[:2] == ("worktree", "add"):
            temporary = Path(args[-2])
            temporary.mkdir(parents=True)
            if self.remote_symlink:
                (temporary / "codex").symlink_to(self.root / "codex", target_is_directory=True)
        elif args[0] == "-c" and self.conflict:
            code, error = 1, "CONFLICT"
        elif "--diff-filter=U" in args:
            output = "codex/global/AGENTS.md\0"
        elif args[0] == "show":
            output = b"original version" if binary else "original version"
        if binary and isinstance(output, str):
            output = output.encode()
        return subprocess.CompletedProcess(args, code, output, error)


def test_clean_worktree_retries_push(repository, monkeypatch):
    git = GitScenario(repository)
    monkeypatch.setattr("src.transport.run_git", git)
    assert sync_repository(repository, ["codex/global/AGENTS.md"])
    assert any(args[0] == "push" for _, args in git.calls)
    assert not any(args[0] == "commit" for _, args in git.calls)


def test_commit_scopes_paths_and_preserves_unrelated_index(repository, monkeypatch):
    git = GitScenario(repository, changed=True, dirty=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    commits = [args for _, args in git.calls if args[0] == "commit"]
    assert len(commits) == 1
    assert "--only" in commits[0]
    assert commits[0][-2:] == ("--", "codex/global/AGENTS.md")
    assert not any(args[0] in {"fetch", "push", "reset"} for _, args in git.calls)


def test_clean_merge_runs_outside_live_files_then_fast_forwards(repository, monkeypatch):
    git = GitScenario(repository, divergent=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert sync_repository(repository, ["codex/global/AGENTS.md"])
    merges = [(path, args) for path, args in git.calls if "merge" in args]
    assert merges[0][0] != repository
    assert merges[1] == (repository, ("merge", "--ff-only", "merged-head"))
    assert any(args[:2] == ("worktree", "remove") for _, args in git.calls)


def test_merge_conflict_saves_versions_never_publishes(repository, monkeypatch):
    git = GitScenario(repository, divergent=True, conflict=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    assert (repository / "codex" / "global" / "AGENTS.md").read_text() == "Instructions"
    saved = repository / ".mesy-local" / "git-conflicts" / "local-head-remote-head"
    assert (saved / "local" / "codex" / "global" / "AGENTS.md").read_bytes() == b"original version"
    assert not any(args[0] == "push" or "--ff-only" in args for _, args in git.calls)


def test_offline_failure_leaves_local_commit_for_retry(repository, monkeypatch):
    git = GitScenario(repository, changed=True, offline=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    assert any(args[0] == "commit" for _, args in git.calls)
    git.changed = False
    git.offline = False
    assert sync_repository(repository, ["codex/global/AGENTS.md"])
    assert any(args[0] == "push" for _, args in git.calls)


def test_head_race_defers_publication(repository, monkeypatch):
    git = GitScenario(repository, divergent=True, race=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    assert not any("--ff-only" in args for _, args in git.calls)


def test_remote_symlink_never_replaces_live_instructions(repository, monkeypatch):
    git = GitScenario(repository, divergent=True, remote_symlink=True)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    assert not any("--ff-only" in args for _, args in git.calls)


@pytest.mark.parametrize("path", [".", "../outside", ".git", ".mesy-local", "/outside"])
def test_unsafe_staging_path_rejected(repository, monkeypatch, path):
    git = GitScenario(repository)
    monkeypatch.setattr("src.transport.run_git", git)
    assert not sync_repository(repository, [path])
    assert not any(args[0] in {"add", "commit", "push"} for _, args in git.calls)


def test_timeout_is_recoverable(repository, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 30)
    monkeypatch.setattr("src.transport.run_git", timeout)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])


def test_empty_remote_branch_is_initialized_without_force(repository, monkeypatch):
    scenario = GitScenario(repository, offline=True)
    def empty_remote(data_dir, *args, **kwargs):
        if args[0] == "ls-remote":
            return subprocess.CompletedProcess(args, 2, "", "")
        return scenario(data_dir, *args, **kwargs)
    monkeypatch.setattr("src.transport.run_git", empty_remote)
    assert sync_repository(repository, ["codex/global/AGENTS.md"])
    assert any(args == ("push", "origin", "HEAD:refs/heads/main") for _, args in scenario.calls)


def test_push_failure_retries_after_clean_restart(repository, monkeypatch):
    scenario = GitScenario(repository)
    failed = [True]
    def push_failure(data_dir, *args, **kwargs):
        if args[0] == "push" and failed[0]:
            return subprocess.CompletedProcess(args, 128, "", "network unavailable")
        return scenario(data_dir, *args, **kwargs)
    monkeypatch.setattr("src.transport.run_git", push_failure)
    assert not sync_repository(repository, ["codex/global/AGENTS.md"])
    failed[0] = False
    assert sync_repository(repository, ["codex/global/AGENTS.md"])
