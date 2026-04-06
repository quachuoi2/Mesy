"""Tests for mesy/targets/generic.py — setup, scan, adopt_directory, stage dirs."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.targets.generic import GenericTarget


def make_target(env, **kwargs) -> GenericTarget:
    """Build a GenericTarget using the generic_env fixture paths."""
    defaults = dict(
        name="test",
        source=env["source"],
        repo_dir=env["repo_dir"],
    )
    defaults.update(kwargs)
    return GenericTarget(**defaults)


class TestSetup:
    def test_copies_matching_files_and_creates_symlinks(self, generic_env):
        """setup() copies .md files into repo_dir and replaces originals with symlinks."""
        (generic_env["source"] / "notes.md").write_text("# Notes")
        (generic_env["source"] / "todo.md").write_text("# Todo")

        target = make_target(generic_env)
        target.setup()

        # Files exist in repo
        assert (generic_env["repo_dir"] / "notes.md").exists()
        assert (generic_env["repo_dir"] / "todo.md").exists()
        # Originals are now symlinks pointing into repo
        assert (generic_env["source"] / "notes.md").is_symlink()
        assert (generic_env["source"] / "todo.md").is_symlink()

    def test_skips_excluded_files(self, generic_env):
        """setup() does not copy files matching the exclude list."""
        (generic_env["source"] / "notes.md").write_text("# Notes")
        (generic_env["source"] / "secret.md").write_text("# Secret")

        target = make_target(generic_env, exclude=["secret.md"])
        target.setup()

        assert (generic_env["repo_dir"] / "notes.md").exists()
        assert not (generic_env["repo_dir"] / "secret.md").exists()
        # Original secret.md must remain a regular file (not converted to symlink)
        assert not (generic_env["source"] / "secret.md").is_symlink()

    def test_skips_non_matching_file_patterns(self, generic_env):
        """setup() ignores files that don't match file_patterns (e.g. .txt)."""
        (generic_env["source"] / "notes.md").write_text("# Notes")
        (generic_env["source"] / "data.txt").write_text("plain text")

        target = make_target(generic_env, file_patterns=["*.md"])
        target.setup()

        assert (generic_env["repo_dir"] / "notes.md").exists()
        assert not (generic_env["repo_dir"] / "data.txt").exists()
        assert not (generic_env["source"] / "data.txt").is_symlink()

    def test_skips_when_source_does_not_exist(self, generic_env):
        """setup() is a no-op when source directory does not exist."""
        missing_source = generic_env["source"] / "nonexistent"
        target = GenericTarget(
            name="test",
            source=missing_source,
            repo_dir=generic_env["repo_dir"],
        )
        # Should not raise
        target.setup()
        # repo_dir should remain empty (no new files created)
        assert list(generic_env["repo_dir"].iterdir()) == []


class TestScan:
    def test_adopts_new_files_added_after_setup(self, generic_env):
        """scan() picks up new files written to source after the initial setup."""
        (generic_env["source"] / "existing.md").write_text("# Existing")
        target = make_target(generic_env)
        target.setup()

        # Add a new file after setup
        (generic_env["source"] / "new_file.md").write_text("# New")
        target.scan()

        assert (generic_env["repo_dir"] / "new_file.md").exists()
        assert (generic_env["source"] / "new_file.md").is_symlink()

    def test_skips_already_symlinked_files(self, generic_env):
        """scan() does not error or double-process files already symlinked."""
        (generic_env["source"] / "notes.md").write_text("# Notes")
        target = make_target(generic_env)
        target.setup()

        # Running scan again on an already-symlinked file must not raise
        target.scan()

        # Symlink still valid
        assert (generic_env["source"] / "notes.md").is_symlink()
        assert (generic_env["repo_dir"] / "notes.md").read_text() == "# Notes"


class TestAdoptDirectory:
    def test_recurses_into_subdirectories(self, generic_env):
        """_adopt_directory() processes files in subdirectories."""
        sub = generic_env["source"] / "memory"
        sub.mkdir()
        (sub / "notes.md").write_text("# Sub notes")

        target = make_target(generic_env)
        target.setup()

        assert (generic_env["repo_dir"] / "memory" / "notes.md").exists()
        assert (sub / "notes.md").is_symlink()

    def test_creates_reverse_symlinks_for_repo_only_files(self, generic_env):
        """_adopt_directory() symlinks repo files back to source when they don't exist locally."""
        # File exists only in repo (pulled from another device)
        (generic_env["repo_dir"] / "remote_only.md").write_text("# Remote")

        target = make_target(generic_env)
        target.scan()

        # Source directory should now have a symlink pointing to the repo file
        source_link = generic_env["source"] / "remote_only.md"
        assert source_link.is_symlink()
        assert source_link.resolve() == (generic_env["repo_dir"] / "remote_only.md").resolve()

    def test_merges_conflicting_files(self, generic_env, monkeypatch):
        """_adopt_directory() calls merge_conflicting_file when source and repo differ."""
        merge_calls = []

        def fake_merge(local, repo):
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.generic.merge_conflicting_file", fake_merge)

        # Write divergent content to both source and repo
        src_file = generic_env["source"] / "notes.md"
        repo_file = generic_env["repo_dir"] / "notes.md"
        src_file.write_text("# Source version")
        repo_file.write_text("# Repo version (different)")

        target = make_target(generic_env)
        target.setup()

        assert len(merge_calls) == 1
        assert merge_calls[0][0] == src_file
        assert merge_calls[0][1] == repo_file


class TestGetStageDirs:
    def test_returns_relative_path_with_trailing_slash(self, generic_env):
        """get_stage_dirs() returns the repo_subdir relative to REPO_DIR."""
        target = make_target(generic_env)
        stage_dirs = target.get_stage_dirs()

        # generic_env places repo_dir at tmp_repo/openclaw/main
        assert stage_dirs == ["openclaw/main/"]

    def test_falls_back_to_absolute_when_not_under_repo_dir(self, generic_env, tmp_path):
        """get_stage_dirs() uses absolute path when repo_dir is outside REPO_DIR."""
        outside_dir = tmp_path / "elsewhere"
        outside_dir.mkdir()
        target = GenericTarget(
            name="test",
            source=generic_env["source"],
            repo_dir=outside_dir,
        )
        stage_dirs = target.get_stage_dirs()
        assert stage_dirs == [str(outside_dir) + "/"]
