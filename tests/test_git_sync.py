"""Tests for src/git_sync.py — git operations and conflict resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from src.git_sync import (
    commit_and_push,
    has_local_changes,
    pull_remote,
    resolve_conflicts,
)
from src.conflict import merge_conflicting_file


class TestGitHelper:
    def test_git_passes_correct_args(self, tmp_repo, monkeypatch):
        """git() constructs the command with -C repo_base."""
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        from src.git_sync import git

        git("status", "--porcelain", repo_base=tmp_repo)

        assert captured[0][0] == "git"
        assert captured[0][1] == "-C"
        assert captured[0][2] == str(tmp_repo)
        assert captured[0][3] == "status"
        assert captured[0][4] == "--porcelain"

    def test_git_respects_timeout(self, tmp_repo, monkeypatch):
        """git() passes timeout=30 to subprocess.run."""
        kwargs_captured = {}

        def fake_run(cmd, **kwargs):
            kwargs_captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        from src.git_sync import git

        git("log", repo_base=tmp_repo)

        assert kwargs_captured.get("timeout") == 30

    def test_git_never_waits_for_an_editor(self, tmp_repo, monkeypatch):
        """git() sets GIT_EDITOR so rebase --continue cannot hang on an editor."""
        kwargs_captured = {}

        def fake_run(cmd, **kwargs):
            kwargs_captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        from src.git_sync import git

        git("rebase", "--continue", repo_base=tmp_repo)

        assert kwargs_captured["env"]["GIT_EDITOR"] == "true"


class TestHasLocalChanges:
    def test_returns_true_when_changes(self, tmp_repo, monkeypatch):
        """has_local_changes() returns True when porcelain output is non-empty."""
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, " M some_file.md", ""),
        )

        assert has_local_changes(repo_base=tmp_repo) is True

    def test_returns_false_when_clean(self, tmp_repo, monkeypatch):
        """has_local_changes() returns False when porcelain output is empty."""
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )

        assert has_local_changes(repo_base=tmp_repo) is False


class TestCommitAndPush:
    def test_stages_dirs_commits_and_pushes(self, tmp_repo, monkeypatch):
        """commit_and_push() runs add, commit, pull --rebase, push in order."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "status" in cmd:
                # has_local_changes -> True
                return subprocess.CompletedProcess(cmd, 0, "M file.md", "")
            if "diff" in cmd and "--cached" in cmd and "--name-status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "M\tfile.md\n", "")
            if "diff" in cmd and "--cached" in cmd and "--quiet" in cmd:
                # staged changes exist (rc=1 means there are staged changes)
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        commit_and_push(["claude/global/", "claude/projects/"], repo_base=tmp_repo)

        git_subcommands = [c[3] for c in commands if c[0] == "git"]
        assert "add" in git_subcommands
        assert "commit" in git_subcommands
        assert "pull" in git_subcommands
        assert "push" in git_subcommands

        commit_cmd = [c for c in commands if c[0] == "git" and c[3] == "commit"][0]
        commit_msg = commit_cmd[commit_cmd.index("-m") + 1]
        assert "update file.md" in commit_msg

    def test_skips_when_no_changes(self, tmp_repo, monkeypatch):
        """commit_and_push() exits early when there is nothing to commit or push."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "rev-list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        commit_and_push(["global/"], repo_base=tmp_repo)

        git_subcommands = [c[3] for c in commands if c[0] == "git"]
        assert "pull" not in git_subcommands
        assert "push" not in git_subcommands

    def test_pushes_commits_left_by_an_earlier_failed_push(self, tmp_repo, monkeypatch):
        """commit_and_push() pushes when nothing is new but the branch is ahead of origin."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "rev-list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "4\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        commit_and_push(["global/"], repo_base=tmp_repo)

        git_subcommands = [c[3] for c in commands if c[0] == "git"]
        assert "commit" not in git_subcommands
        assert "push" in git_subcommands

    def test_handles_push_failure(self, tmp_repo, monkeypatch, caplog):
        """commit_and_push() logs a warning when push fails (offline)."""
        import logging

        def fake_run(cmd, **kw):
            if "status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "M file.md", "")
            if "diff" in cmd and "--cached" in cmd and "--name-status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "M\tfile.md\n", "")
            if "diff" in cmd and "--cached" in cmd and "--quiet" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to connect")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        with caplog.at_level(logging.WARNING, logger="mesy"):
            commit_and_push(["global/"], repo_base=tmp_repo)

        assert any("push failed" in r.message for r in caplog.records)

    def test_uses_provided_repo_base(self, tmp_path, monkeypatch):
        """commit_and_push() passes the provided repo_base to git."""
        custom_repo_base = tmp_path / "CustomRepo"
        custom_repo_base.mkdir()

        used_dirs = []

        def fake_run(cmd, **kw):
            if cmd[0] == "git":
                used_dirs.append(cmd[2])  # -C <dir>
            if "status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "M file.md", "")
            if "diff" in cmd and "--cached" in cmd and "--name-status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "M\tfile.md\n", "")
            if "diff" in cmd and "--cached" in cmd and "--quiet" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        commit_and_push(["global/"], repo_base=custom_repo_base)

        assert all(d == str(custom_repo_base) for d in used_dirs)


class TestResolveConflicts:
    def test_continues_rebase_when_no_conflicted_files(self, tmp_repo, monkeypatch):
        """resolve_conflicts() calls rebase --continue when no unmerged files."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "diff" in cmd and "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        resolve_conflicts(repo_base=tmp_repo)

        subcommands = [c[3:] for c in commands if c[0] == "git"]
        assert ["rebase", "--continue"] in subcommands

    def test_aborts_if_continue_fails(self, tmp_repo, monkeypatch):
        """resolve_conflicts() calls rebase --abort if --continue fails."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "diff" in cmd and "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "rebase" in cmd and "--continue" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "error")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        resolve_conflicts(repo_base=tmp_repo)

        subcommands = [c[3:] for c in commands if c[0] == "git"]
        assert ["rebase", "--abort"] in subcommands

    def test_keeps_git_markers_and_stages_file(self, tmp_repo, monkeypatch):
        """resolve_conflicts() leaves git's conflict markers as they are and runs git add."""
        conflicted_file = tmp_repo / "claude" / "global" / "memory.md"
        conflicted = (
            "---\ntitle: test\n---\n"
            "<<<<<<< HEAD\nremote content\n"
            "=======\nlocal content\n"
            ">>>>>>> abc123\n"
        )
        conflicted_file.write_text(conflicted)
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "diff" in cmd and "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "claude/global/memory.md", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        resolve_conflicts(repo_base=tmp_repo)

        assert conflicted_file.read_text() == conflicted
        assert ["add", "claude/global/memory.md"] in [c[3:] for c in commands]

    def test_calls_rebase_continue_after_resolution(self, tmp_repo, monkeypatch):
        """resolve_conflicts() calls rebase --continue after resolving files."""
        conflicted_file = tmp_repo / "claude" / "global" / "conflict.md"
        conflicted_file.write_text(
            "<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> abc\n"
        )

        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "diff" in cmd and "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "claude/global/conflict.md", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        resolve_conflicts(repo_base=tmp_repo)

        subcommands = [c[3:] for c in commands if c[0] == "git"]
        assert ["rebase", "--continue"] in subcommands


class TestPullRemote:
    def test_logs_on_success(self, tmp_repo, monkeypatch, caplog):
        """pull_remote() logs info when new changes are pulled."""
        import logging

        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "Fast-forward", ""),
        )

        with caplog.at_level(logging.INFO, logger="mesy"):
            pull_remote(repo_base=tmp_repo)

        assert any("Pulled" in r.message for r in caplog.records)

    def test_warns_on_failure(self, tmp_repo, monkeypatch, caplog):
        """pull_remote() logs a warning on failure, does not raise."""
        import logging

        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "connection refused"),
        )

        with caplog.at_level(logging.WARNING, logger="mesy"):
            pull_remote(repo_base=tmp_repo)  # must not raise

        assert any(r.message == "pull failed: connection refused" for r in caplog.records)

    def test_resolves_conflicts_on_failure(self, tmp_repo, monkeypatch):
        """pull_remote() resolves conflicts instead of leaving the rebase stuck."""
        conflicted_file = tmp_repo / "claude" / "global" / "conflict.md"
        conflicted_file.write_text("<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> abc\n")

        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            if "pull" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "CONFLICT")
            if "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "claude/global/conflict.md", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        pull_remote(repo_base=tmp_repo)

        assert "<<<<<<<" in conflicted_file.read_text()
        assert ["rebase", "--continue"] in [c[3:] for c in commands]


BASE_DESCRIPTION = "Commit messages are imperative, one line per change"


def memory_file(description: str, extra_body: str = "") -> str:
    return (
        "---\nname: commit-style\n"
        f"description: {description}\n"
        "metadata:\n  type: feedback\n---\n\n"
        "Never add Co-Authored-By lines.\n"
        f"{extra_body}"
    )


class TestMergeConflictingFile:
    """Real `git merge-file` runs here: it only reads and writes files, never touches a repo."""

    def test_merges_edits_that_do_not_overlap(self, tmp_path):
        """Repo changed the description, local added a body line: both kept, no markers."""
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text(memory_file(BASE_DESCRIPTION, "Short notes preferred.\n"))
        repo.write_text(memory_file("Commit messages: imperative subject, no attribution"))

        merge_conflicting_file(local=local, repo=repo, base_text=memory_file(BASE_DESCRIPTION))

        assert repo.read_text() == memory_file(
            "Commit messages: imperative subject, no attribution", "Short notes preferred.\n"
        )

    def test_marks_only_overlapping_lines(self, tmp_path):
        """Both sides changed the description: only that line is marked, nested frontmatter stays intact."""
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text(memory_file("Commit messages: imperative, short notes"))
        repo.write_text(memory_file("Commit messages: imperative subject, no attribution"))

        merge_conflicting_file(local=local, repo=repo, base_text=memory_file(BASE_DESCRIPTION))

        result = repo.read_text()
        assert result.count("<<<<<<<") == 1
        assert (
            "description: Commit messages: imperative, short notes\n"
            "=======\n"
            "description: Commit messages: imperative subject, no attribution\n"
            ">>>>>>> repo\n"
            "metadata:\n  type: feedback\n"
        ) in result

    def test_empty_base_marks_every_difference(self, tmp_path):
        """No last synced version: differing lines are marked, shared lines appear once."""
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text("# Notes\nlocal line\n")
        repo.write_text("# Notes\nrepo line\n")

        merge_conflicting_file(local=local, repo=repo)

        result = repo.read_text()
        assert result.count("# Notes") == 1
        assert "local line\n=======\nrepo line\n>>>>>>> repo\n" in result

    def test_local_and_repo_identical_after_merge(self, tmp_path):
        """merge_conflicting_file() writes identical content to both local and repo.

        This prevents an infinite commit loop for copy-mode targets where local
        and repo are separate files: if only repo is written, filecmp.cmp() would
        return False on the next scan cycle, triggering another merge indefinitely.
        """
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text("# Local\nLocal content.\n")
        repo.write_text("# Repo\nRepo content.\n")

        merge_conflicting_file(local=local, repo=repo)

        assert local.read_text() == repo.read_text()

    def test_raises_when_git_cannot_merge(self, tmp_path):
        """A binary file makes git merge-file fail; the files are left untouched."""
        local = tmp_path / "local.bin"
        repo = tmp_path / "repo.bin"
        local.write_bytes(b"a\0b")
        repo.write_bytes(b"c\0d")

        with pytest.raises(RuntimeError, match="merge-file failed"):
            merge_conflicting_file(local=local, repo=repo)

        assert repo.read_bytes() == b"c\0d"


class TestCurrentBranch:
    """current_branch() must follow the checked-out branch, not assume main."""

    def test_derives_checked_out_branch(self, monkeypatch):
        from src.git_sync import current_branch

        monkeypatch.setattr("src.git_sync.git",
                            lambda *a, **kw: subprocess.CompletedProcess(a, 0, "memory-sync\n", ""))

        assert current_branch(Path("/tmp")) == "memory-sync"

    def test_falls_back_to_main_on_detached_head(self, monkeypatch):
        from src.git_sync import current_branch

        monkeypatch.setattr("src.git_sync.git",
                            lambda *a, **kw: subprocess.CompletedProcess(a, 128, "", "fatal"))

        assert current_branch(Path("/tmp")) == "main"
