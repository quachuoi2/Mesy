"""Tests for mesy/targets/generic.py — setup, scan, adopt_directory, stage dirs."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.targets.generic import GenericTarget


def make_target(env, **kwargs) -> GenericTarget:
    """Build a GenericTarget using the generic_env fixture paths."""
    defaults = dict(
        name="test",
        src=env["src"],
        target_dir=env["target_dir"],
        repo_base=env["tmp_repo"],
    )
    defaults.update(kwargs)
    return GenericTarget(**defaults)


class TestSetup:
    def test_copies_matching_files_and_creates_symlinks(self, generic_env):
        """setup() copies .md files into target_dir and replaces originals with symlinks."""
        (generic_env["src"] / "notes.md").write_text("# Notes")
        (generic_env["src"] / "todo.md").write_text("# Todo")

        target = make_target(generic_env)
        target.setup()

        # Files exist in repo
        assert (generic_env["target_dir"] / "notes.md").exists()
        assert (generic_env["target_dir"] / "todo.md").exists()
        # Originals are now symlinks pointing into repo
        assert (generic_env["src"] / "notes.md").is_symlink()
        assert (generic_env["src"] / "todo.md").is_symlink()

    def test_skips_excluded_files(self, generic_env):
        """setup() does not copy files matching the exclude list."""
        (generic_env["src"] / "notes.md").write_text("# Notes")
        (generic_env["src"] / "secret.md").write_text("# Secret")

        target = make_target(generic_env, exclude=["secret.md"])
        target.setup()

        assert (generic_env["target_dir"] / "notes.md").exists()
        assert not (generic_env["target_dir"] / "secret.md").exists()
        # Original secret.md must remain a regular file (not converted to symlink)
        assert not (generic_env["src"] / "secret.md").is_symlink()

    def test_skips_non_matching_file_patterns(self, generic_env):
        """setup() ignores files that don't match file_patterns (e.g. .txt)."""
        (generic_env["src"] / "notes.md").write_text("# Notes")
        (generic_env["src"] / "data.txt").write_text("plain text")

        target = make_target(generic_env, file_patterns=["*.md"])
        target.setup()

        assert (generic_env["target_dir"] / "notes.md").exists()
        assert not (generic_env["target_dir"] / "data.txt").exists()
        assert not (generic_env["src"] / "data.txt").is_symlink()

    def test_skips_when_src_does_not_exist(self, generic_env):
        """setup() is a no-op when source directory does not exist."""
        missing_src = generic_env["src"] / "nonexistent"
        target = GenericTarget(
            name="test",
            src=missing_src,
            target_dir=generic_env["target_dir"],
        )
        # Should not raise
        target.setup()
        # target_dir should remain empty (no new files created)
        assert list(generic_env["target_dir"].iterdir()) == []


class TestScan:
    def test_adopts_new_files_added_after_setup(self, generic_env):
        """scan() picks up new files written to source after the initial setup."""
        (generic_env["src"] / "existing.md").write_text("# Existing")
        target = make_target(generic_env)
        target.setup()

        # Add a new file after setup
        (generic_env["src"] / "new_file.md").write_text("# New")
        target.scan()

        assert (generic_env["target_dir"] / "new_file.md").exists()
        assert (generic_env["src"] / "new_file.md").is_symlink()

    def test_skips_already_symlinked_files(self, generic_env):
        """scan() does not error or double-process files already symlinked."""
        (generic_env["src"] / "notes.md").write_text("# Notes")
        target = make_target(generic_env)
        target.setup()

        # Running scan again on an already-symlinked file must not raise
        target.scan()

        # Symlink still valid
        assert (generic_env["src"] / "notes.md").is_symlink()
        assert (generic_env["target_dir"] / "notes.md").read_text() == "# Notes"


class TestAdoptDirectory:
    def test_recurses_into_subdirectories(self, generic_env):
        """_adopt_directory() processes files in subdirectories."""
        sub = generic_env["src"] / "memory"
        sub.mkdir()
        (sub / "notes.md").write_text("# Sub notes")

        target = make_target(generic_env)
        target.setup()

        assert (generic_env["target_dir"] / "memory" / "notes.md").exists()
        assert (sub / "notes.md").is_symlink()

    def test_creates_reverse_symlinks_for_repo_only_files(self, generic_env):
        """_adopt_directory() symlinks repo files back to source when they don't exist locally."""
        # File exists only in repo (pulled from another device)
        (generic_env["target_dir"] / "remote_only.md").write_text("# Remote")

        target = make_target(generic_env)
        target.scan()

        # Source directory should now have a symlink pointing to the repo file
        src_link = generic_env["src"] / "remote_only.md"
        assert src_link.is_symlink()
        assert src_link.resolve() == (generic_env["target_dir"] / "remote_only.md").resolve()

    def test_replaces_broken_symlink_with_correct_symlink(self, generic_env):
        """_adopt_directory() replaces a broken symlink at the source path with a correct one."""
        repo_file = generic_env["target_dir"] / "remote_only.md"
        repo_file.write_text("# Remote")

        # Create a broken symlink at the would-be source path
        src_link = generic_env["src"] / "remote_only.md"
        src_link.symlink_to(generic_env["src"] / "nonexistent_target.md")
        assert src_link.is_symlink()
        assert not src_link.exists()  # confirms it's broken

        target = make_target(generic_env)
        target.scan()

        # Broken symlink should now point correctly to the repo file
        assert src_link.is_symlink()
        assert src_link.exists()
        assert src_link.resolve() == repo_file.resolve()

    def test_merges_conflicting_files(self, generic_env, monkeypatch):
        """_adopt_directory() calls merge_conflicting_file when source and repo differ."""
        merge_calls = []

        def fake_merge(local, repo):
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.generic.merge_conflicting_file", fake_merge)

        # Write divergent content to both source and repo
        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"
        src_file.write_text("# Source version")
        repo_file.write_text("# Repo version (different)")

        target = make_target(generic_env)
        target.setup()

        assert len(merge_calls) == 1
        assert merge_calls[0][0] == src_file
        assert merge_calls[0][1] == repo_file


class TestAdoptDirectoryCopy:
    def test_copy_mode_files_copied_src_to_repo(self, generic_env):
        """copy mode: source files are copied into repo (not symlinked)."""
        (generic_env["src"] / "notes.md").write_text("# Notes")
        (generic_env["src"] / "todo.md").write_text("# Todo")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert (generic_env["target_dir"] / "notes.md").exists()
        assert (generic_env["target_dir"] / "todo.md").exists()
        # Source files must be regular files, not symlinks
        assert not (generic_env["src"] / "notes.md").is_symlink()
        assert not (generic_env["src"] / "todo.md").is_symlink()

    def test_copy_mode_files_copied_repo_to_src(self, generic_env):
        """copy mode: repo-only files (pulled from another device) are copied to source."""
        (generic_env["target_dir"] / "remote_only.md").write_text("# From remote")

        target = make_target(generic_env, sync_mode="copy")
        target.scan()

        src_file = generic_env["src"] / "remote_only.md"
        assert src_file.exists()
        assert not src_file.is_symlink()
        assert src_file.read_text() == "# From remote"

    def test_copy_mode_no_symlinks_created(self, generic_env):
        """copy mode: no symlinks appear in either source or target_dir after adoption."""
        (generic_env["src"] / "a.md").write_text("A")
        (generic_env["target_dir"] / "b.md").write_text("B")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        for f in generic_env["src"].iterdir():
            assert not f.is_symlink(), f"{f} should not be a symlink in copy mode"
        for f in generic_env["target_dir"].iterdir():
            assert not f.is_symlink(), f"{f} should not be a symlink in copy mode"

    def test_copy_mode_symlink_to_copy_migration(self, generic_env):
        """copy mode: leftover symlinks in source are migrated to real files."""
        repo_file = generic_env["target_dir"] / "notes.md"
        repo_file.write_text("# Repo content")

        src_link = generic_env["src"] / "notes.md"
        src_link.symlink_to(repo_file)
        assert src_link.is_symlink()

        target = make_target(generic_env, sync_mode="copy")
        target.scan()

        assert not src_link.is_symlink()
        assert src_link.is_file()
        assert src_link.read_text() == "# Repo content"

    def test_copy_mode_symlink_with_missing_target_restored_from_repo(self, generic_env):
        """copy mode: broken symlink in source is replaced with real copy from repo."""
        repo_file = generic_env["target_dir"] / "notes.md"
        repo_file.write_text("# Repo content")

        src_link = generic_env["src"] / "notes.md"
        # Point at something that doesn't exist
        src_link.symlink_to(generic_env["src"] / "nonexistent.md")
        assert src_link.is_symlink()
        assert not src_link.exists()  # broken

        target = make_target(generic_env, sync_mode="copy")
        target.scan()

        assert not src_link.is_symlink()
        assert src_link.read_text() == "# Repo content"

    def test_copy_mode_symlink_with_no_resolvable_target_is_skipped(self, generic_env, caplog):
        """copy mode: broken symlink with no repo fallback is skipped with a warning."""
        import logging

        src_link = generic_env["src"] / "orphan.md"
        src_link.symlink_to(generic_env["src"] / "ghost.md")
        assert src_link.is_symlink()
        assert not src_link.exists()

        target = make_target(generic_env, sync_mode="copy")
        with caplog.at_level(logging.WARNING, logger="mesy"):
            target.scan()

        # File should not have been created (symlink removed but nothing written)
        assert not (generic_env["target_dir"] / "orphan.md").exists()

    def test_copy_mode_conflict_calls_merge(self, generic_env, monkeypatch, isolated_sync_state):
        """copy mode: both-sides-changed triggers merge_conflicting_file (real conflict)."""
        merge_calls = []

        def fake_merge(local, repo):
            merge_calls.append((local, repo))
            # Simulate what merge_conflicting_file does: write merged content to both
            merged = "<!-- merged -->"
            repo.write_text(merged)
            local.write_text(merged)

        monkeypatch.setattr("src.targets.generic.merge_conflicting_file", fake_merge)

        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"

        # Write a known base state and record its hash
        base_content = "# Base version"
        src_file.write_text(base_content)
        repo_file.write_text(base_content)
        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        isolated_sync_state.set_hash(key, base_content)

        # Now both sides diverge independently
        src_file.write_text("# Source changed")
        repo_file.write_text("# Repo changed independently")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert len(merge_calls) == 1
        assert merge_calls[0][0] == src_file
        assert merge_calls[0][1] == repo_file


class TestThreeWaySync:
    def test_src_wins_when_only_src_changed(
        self, generic_env, isolated_sync_state
    ):
        """Three-way sync: source changed, repo unchanged → copy source to repo."""
        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"

        base = "# Base"
        src_file.write_text(base)
        repo_file.write_text(base)
        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        isolated_sync_state.set_hash(key, base)

        src_file.write_text("# Source updated")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert repo_file.read_text() == "# Source updated"
        assert src_file.read_text() == "# Source updated"

    def test_repo_wins_when_only_repo_changed(self, generic_env, isolated_sync_state):
        """Three-way sync: repo changed, source unchanged → copy repo to source."""
        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"

        base = "# Base"
        src_file.write_text(base)
        repo_file.write_text(base)
        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        isolated_sync_state.set_hash(key, base)

        repo_file.write_text("# Repo updated")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert src_file.read_text() == "# Repo updated"
        assert repo_file.read_text() == "# Repo updated"

    def test_no_action_when_files_match(self, generic_env, isolated_sync_state, monkeypatch):
        """Three-way sync: identical files → no copy, no merge."""
        merge_calls = []
        monkeypatch.setattr(
            "src.targets.generic.merge_conflicting_file",
            lambda local, repo: merge_calls.append((local, repo)),
        )

        content = "# Same content"
        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"
        src_file.write_text(content)
        repo_file.write_text(content)
        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        isolated_sync_state.set_hash(key, content)

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert merge_calls == []
        assert repo_file.read_text() == content

    def test_src_wins_on_first_sync_no_base_hash(self, generic_env, monkeypatch):
        """Three-way sync: no base hash (first sync) → source wins, no conflict."""
        merge_calls = []
        monkeypatch.setattr(
            "src.targets.generic.merge_conflicting_file",
            lambda local, repo: merge_calls.append((local, repo)),
        )

        src_file = generic_env["src"] / "notes.md"
        repo_file = generic_env["target_dir"] / "notes.md"
        src_file.write_text("# Source content")
        repo_file.write_text("# Repo content (no base hash)")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert merge_calls == []
        assert repo_file.read_text() == "# Source content"

    def test_hash_stored_after_new_file_copy(self, generic_env, isolated_sync_state):
        """After copying a new source file to repo, the hash is stored."""
        src_file = generic_env["src"] / "notes.md"
        src_file.write_text("# Notes")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        repo_file = generic_env["target_dir"] / "notes.md"
        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        assert isolated_sync_state.get_hash(key) == isolated_sync_state.compute_hash("# Notes")

    def test_hash_stored_after_repo_to_src_copy(self, generic_env, isolated_sync_state):
        """After copying a repo-only file to source, the hash is stored."""
        repo_file = generic_env["target_dir"] / "remote.md"
        repo_file.write_text("# Remote")

        target = make_target(generic_env, sync_mode="copy")
        target.scan()

        key = str(repo_file.relative_to(generic_env["tmp_repo"]))
        assert isolated_sync_state.get_hash(key) == isolated_sync_state.compute_hash("# Remote")

    def test_copy_mode_recurses_into_subdirectories(self, generic_env):
        """copy mode: files in source subdirectories are copied into matching repo subdirs."""
        sub = generic_env["src"] / "memory"
        sub.mkdir()
        (sub / "project_notes.md").write_text("# Project Notes")

        target = make_target(generic_env, sync_mode="copy")
        target.setup()

        assert (generic_env["target_dir"] / "memory" / "project_notes.md").exists()
        assert not (sub / "project_notes.md").is_symlink()

    def test_copy_mode_leftover_symlink_in_pass2_replaced_with_copy(self, generic_env):
        """copy mode pass 2: leftover symlink in source position is replaced with a real copy."""
        repo_file = generic_env["target_dir"] / "remote.md"
        repo_file.write_text("# Remote")

        # Simulate a leftover symlink in source for a repo file
        src_link = generic_env["src"] / "remote.md"
        src_link.symlink_to(repo_file)
        assert src_link.is_symlink()

        target = make_target(generic_env, sync_mode="copy")
        target.scan()

        # Symlink should be replaced with a real file
        assert not src_link.is_symlink()
        assert src_link.read_text() == "# Remote"


class TestGetStageDirs:
    def test_returns_relative_path_with_trailing_slash(self, generic_env):
        """get_stage_dirs() returns the repo_subdir relative to MESY_DIR."""
        target = make_target(generic_env)
        stage_dirs = target.get_stage_dirs()

        # generic_env places target_dir at tmp_repo/openclaw/main
        assert stage_dirs == ["openclaw/main/"]

    def test_falls_back_to_absolute_when_target_dir_outside_repo_base(self, generic_env, tmp_path):
        """get_stage_dirs() uses absolute path when target_dir is outside repo_base."""
        outside_dir = tmp_path / "elsewhere"
        outside_dir.mkdir()
        # repo_base must be explicitly set to something that does NOT contain outside_dir
        unrelated_repo_base = tmp_path / "unrelated_data"
        unrelated_repo_base.mkdir()
        target = GenericTarget(
            name="test",
            src=generic_env["src"],
            target_dir=outside_dir,
            repo_base=unrelated_repo_base,
        )
        stage_dirs = target.get_stage_dirs()
        assert stage_dirs == [str(outside_dir) + "/"]
