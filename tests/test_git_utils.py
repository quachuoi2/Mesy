"""Tests for mesy/git_utils.py — git operations and conflict resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from src.git_utils import (
    commit_and_push,
    has_local_changes,
    merge_conflicting_file,
    pull_remote,
    resolve_conflicts,
    split_frontmatter,
)


class TestGitHelper:
    def test_git_passes_correct_args(self, tmp_repo, monkeypatch):
        """git() constructs the command with -C REPO_DIR."""
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        from src.git_utils import git
        git("status", "--porcelain")

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

        from src.git_utils import git
        git("log")

        assert kwargs_captured.get("timeout") == 30


class TestHasLocalChanges:
    def test_returns_true_when_changes(self, tmp_repo, monkeypatch):
        """has_local_changes() returns True when porcelain output is non-empty."""
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, " M some_file.md", ""),
        )
        assert has_local_changes() is True

    def test_returns_false_when_clean(self, tmp_repo, monkeypatch):
        """has_local_changes() returns False when porcelain output is empty."""
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert has_local_changes() is False


class TestCommitAndPush:
    def test_stages_dirs_commits_and_pushes(self, tmp_repo, monkeypatch):
        """commit_and_push() runs add, commit, pull --rebase, push in order."""
        commands = []
        call_count = [0]

        def fake_run(cmd, **kw):
            commands.append(cmd)
            call_count[0] += 1
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
        commit_and_push(["claude/global/", "claude/projects/"])

        git_subcommands = [c[3] for c in commands if c[0] == "git"]
        assert "add" in git_subcommands
        assert "commit" in git_subcommands
        assert "pull" in git_subcommands
        assert "push" in git_subcommands

        # Verify commit message includes changed files
        commit_cmd = [c for c in commands if c[0] == "git" and c[3] == "commit"][0]
        commit_msg = commit_cmd[commit_cmd.index("-m") + 1]
        assert "update file.md" in commit_msg

    def test_skips_when_no_changes(self, tmp_repo, monkeypatch):
        """commit_and_push() exits early when has_local_changes() is False."""
        commands = []

        def fake_run(cmd, **kw):
            commands.append(cmd)
            # status returns empty -> no changes
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)
        commit_and_push(["global/"])

        # Only the status check should have run
        git_subcommands = [c[3] for c in commands if c[0] == "git"]
        assert git_subcommands == ["status"]

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
            commit_and_push(["global/"])

        assert any("push failed" in r.message for r in caplog.records)


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
        resolve_conflicts()

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
        resolve_conflicts()

        subcommands = [c[3:] for c in commands if c[0] == "git"]
        assert ["rebase", "--abort"] in subcommands

    def test_resolves_conflict_markers_and_adds_frontmatter(self, tmp_repo, monkeypatch):
        """resolve_conflicts() rewrites conflict markers and adds conflict:true frontmatter."""
        conflicted_file = tmp_repo / "claude" / "global" / "memory.md"
        conflicted_file.write_text(
            "---\ntitle: test\n---\n"
            "<<<<<<< HEAD\nlocal content\n"
            "=======\nremote content\n"
            ">>>>>>> abc123\n"
        )

        def fake_run(cmd, **kw):
            if "diff" in cmd and "--diff-filter=U" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "claude/global/memory.md", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)
        resolve_conflicts()

        content = conflicted_file.read_text()
        assert "<<<<<<<" not in content
        assert "local content" in content
        assert "remote content" in content
        assert "conflict: true" in content

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
        resolve_conflicts()

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
            pull_remote()

        assert any("Pulled" in r.message for r in caplog.records)

    def test_handles_failure_silently(self, tmp_repo, monkeypatch, caplog):
        """pull_remote() logs debug on failure, does not raise."""
        import logging

        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "connection refused"),
        )

        with caplog.at_level(logging.DEBUG, logger="mesy"):
            pull_remote()  # must not raise

        assert any("pull failed" in r.message for r in caplog.records)


class TestSplitFrontmatter:
    def test_parses_frontmatter(self):
        """split_frontmatter() extracts key:value pairs from YAML header."""
        text = "---\ntitle: hello\nconflict: true\n---\nbody text\n"
        body, fm = split_frontmatter(text)
        assert fm["title"] == "hello"
        assert fm["conflict"] == "true"
        assert "body text" in body

    def test_no_frontmatter(self):
        """split_frontmatter() returns empty dict when there's no --- header."""
        text = "Just some plain text.\nNo frontmatter here.\n"
        body, fm = split_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_incomplete_frontmatter(self):
        """split_frontmatter() handles malformed frontmatter gracefully."""
        text = "---\njust one delimiter\n"
        body, fm = split_frontmatter(text)
        assert fm == {}
        assert body == text


class TestMergeConflictingFile:
    def test_merges_both_versions(self, tmp_path):
        """merge_conflicting_file() writes both local and repo versions with markers."""
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text("# Local\nThis is the local version.\n")
        repo.write_text("# Repo\nThis is the repo version.\n")

        merge_conflicting_file(local=local, repo=repo)

        result = repo.read_text()
        assert "local version" in result
        assert "repo version" in result
        assert "CONFLICT" in result
        assert "conflict: true" in result

    def test_merges_frontmatter(self, tmp_path):
        """merge_conflicting_file() merges frontmatter from both files."""
        local = tmp_path / "local.md"
        repo = tmp_path / "repo.md"
        local.write_text("---\nauthor: local\n---\nlocal body\n")
        repo.write_text("---\nauthor: repo\ndate: 2024\n---\nrepo body\n")

        merge_conflicting_file(local=local, repo=repo)

        result = repo.read_text()
        assert "conflict: true" in result
        # local frontmatter wins for shared keys
        assert "author: local" in result
        # repo-only key is preserved
        assert "date: 2024" in result

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
