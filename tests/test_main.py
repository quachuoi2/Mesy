"""Tests for mesy/main.py — target building, stage dir collection, mtime scanning, main()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.main import build_targets, get_all_stage_dirs, get_md_mtimes
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget


class TestBuildTargets:
    def test_claude_code_type_produces_claude_code_target(self, tmp_repo):
        """build_targets() creates a ClaudeCodeTarget for type='claude-code'."""
        tdef = {
            "name": "claude-code",
            "type": "claude-code",
            "source": Path("/tmp/src"),
            "repo_subdir": "projects",
        }
        targets = build_targets([tdef])
        assert len(targets) == 1
        assert isinstance(targets[0], ClaudeCodeTarget)

    def test_generic_type_produces_generic_target(self, tmp_repo):
        """build_targets() creates a GenericTarget for type='generic'."""
        tdef = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md"],
            "exclude": [],
        }
        targets = build_targets([tdef])
        assert len(targets) == 1
        assert isinstance(targets[0], GenericTarget)
        assert targets[0].name == "openclaw"

    def test_generic_target_inherits_file_patterns_and_exclude(self, tmp_repo):
        """build_targets() passes file_patterns and exclude through to GenericTarget."""
        tdef = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md", "*.txt"],
            "exclude": ["secret.md"],
        }
        targets = build_targets([tdef])
        assert targets[0].file_patterns == ["*.md", "*.txt"]
        assert targets[0].exclude == ["secret.md"]

    def test_unknown_type_emits_warning(self, tmp_repo, caplog):
        """build_targets() logs a warning for unrecognised target types."""
        import logging
        tdef = {
            "name": "mystery",
            "type": "not-a-real-type",
            "source": Path("/tmp/src"),
            "repo_subdir": "mystery",
        }
        with caplog.at_level(logging.WARNING, logger="mesy"):
            targets = build_targets([tdef])
        assert targets == []
        assert "Unknown target type" in caplog.text

    def test_mixed_target_defs_build_correct_types(self, tmp_repo):
        """build_targets() handles a list with both claude-code and generic entries."""
        tdefs = [
            {
                "name": "claude-code",
                "type": "claude-code",
                "source": Path("/tmp/claude"),
                "repo_subdir": "projects",
            },
            {
                "name": "openclaw",
                "type": "generic",
                "source": Path("/tmp/openclaw"),
                "repo_subdir": "openclaw/main",
                "file_patterns": ["*.md"],
                "exclude": [],
            },
        ]
        targets = build_targets(tdefs)
        assert len(targets) == 2
        assert isinstance(targets[0], ClaudeCodeTarget)
        assert isinstance(targets[1], GenericTarget)


class TestGetAllStageDirs:
    def test_deduplicates_identical_dirs_across_targets(self, tmp_repo):
        """get_all_stage_dirs() returns unique dirs even when targets share them."""
        t1 = MagicMock()
        t1.get_stage_dirs.return_value = ["projects/", "global/"]
        t2 = MagicMock()
        t2.get_stage_dirs.return_value = ["projects/"]  # duplicate

        result = get_all_stage_dirs([t1, t2])
        assert result == ["projects/", "global/"]

    def test_preserves_order_of_first_occurrence(self, tmp_repo):
        """get_all_stage_dirs() preserves insertion order, deduplicating on first seen."""
        t1 = MagicMock()
        t1.get_stage_dirs.return_value = ["a/", "b/"]
        t2 = MagicMock()
        t2.get_stage_dirs.return_value = ["b/", "c/"]

        result = get_all_stage_dirs([t1, t2])
        assert result == ["a/", "b/", "c/"]

    def test_empty_target_list_returns_empty(self, tmp_repo):
        """get_all_stage_dirs() returns empty list for no targets."""
        assert get_all_stage_dirs([]) == []


class TestGetMdMtimes:
    def test_finds_md_files_in_repo(self, tmp_repo):
        """get_md_mtimes() returns mtimes for all .md files in REPO_DIR."""
        (tmp_repo / "notes.md").write_text("# Notes")
        (tmp_repo / "global" / "CLAUDE.md").write_text("# Global")

        result = get_md_mtimes()

        assert any("notes.md" in k for k in result)
        assert any("CLAUDE.md" in k for k in result)

    def test_skips_dotgit_directory(self, tmp_repo):
        """get_md_mtimes() does not include files under .git/."""
        git_dir = tmp_repo / ".git" / "COMMIT_EDITMSG"
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        git_dir.write_text("init")

        # Put a .md file inside .git to confirm exclusion
        (tmp_repo / ".git" / "hidden.md").write_text("should be ignored")
        (tmp_repo / "visible.md").write_text("should appear")

        result = get_md_mtimes()

        assert not any(".git" in k for k in result)
        assert any("visible.md" in k for k in result)

    def test_skips_non_md_files(self, tmp_repo):
        """get_md_mtimes() ignores .txt and other non-.md files."""
        (tmp_repo / "readme.txt").write_text("text file")
        (tmp_repo / "data.md").write_text("# Data")

        result = get_md_mtimes()

        assert not any("readme.txt" in k for k in result)
        assert any("data.md" in k for k in result)


class TestMain:
    def test_main_runs_initial_sync_cycle(self, tmp_repo, monkeypatch):
        """main() loads config, builds targets, calls pull_remote, scan, commit_and_push."""
        from configparser import ConfigParser
        from src.targets.base import SyncTarget

        # Write a minimal sync.conf so load_targets returns one generic target
        conf_text = (
            "[target:openclaw]\n"
            f"source = {tmp_repo / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_repo / "sync.conf").write_text(conf_text)

        pull_mock = MagicMock()
        push_mock = MagicMock()
        scan_mock = MagicMock()

        monkeypatch.setattr("src.main.pull_remote", pull_mock)
        monkeypatch.setattr("src.main.commit_and_push", push_mock)

        # Prevent the daemon loop from starting after initial sync
        import src.main as main_mod

        original_build = main_mod.build_targets

        def patched_build(target_defs):
            targets = original_build(target_defs)
            for t in targets:
                t.scan = scan_mock
            return targets

        monkeypatch.setattr("src.main.build_targets", patched_build)

        # Prevent the watchdog/polling loop from running
        monkeypatch.setattr("src.main.run_with_watchdog", lambda *a, **kw: None)
        monkeypatch.setattr("src.main.run_with_polling", lambda *a, **kw: None)

        from src.main import main
        main()

        pull_mock.assert_called_once()
        scan_mock.assert_called()
        push_mock.assert_called_once()
