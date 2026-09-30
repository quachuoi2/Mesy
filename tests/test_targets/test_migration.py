"""Unit tests for src/targets/_migration.py — ensure_real_file and ensure_real_dir."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from src.targets._migration import ensure_real_dir, ensure_real_file


class TestEnsureRealFile:
    def test_real_file_is_noop_returns_false(self, tmp_path: Path) -> None:
        """A real file is left untouched and returns False."""
        f = tmp_path / "notes.md"
        f.write_text("content")

        result = ensure_real_file(f, repo_fallback=None)

        assert result is False
        assert f.is_file()
        assert not f.is_symlink()
        assert f.read_text() == "content"

    def test_missing_path_is_noop_returns_false(self, tmp_path: Path) -> None:
        """A path that does not exist returns False without raising."""
        f = tmp_path / "nonexistent.md"

        result = ensure_real_file(f, repo_fallback=None)

        assert result is False
        assert not f.exists()

    def test_symlink_with_existing_target_materialises_and_returns_true(
        self, tmp_path: Path
    ) -> None:
        """Symlink whose target exists is replaced with a real file; content preserved."""
        target = tmp_path / "real.md"
        target.write_text("real content")

        link = tmp_path / "CLAUDE.md"
        link.symlink_to(target)
        assert link.is_symlink()

        result = ensure_real_file(link, repo_fallback=None)

        assert result is True
        assert not link.is_symlink()
        assert link.is_file()
        assert link.read_text() == "real content"

    def test_symlink_with_missing_target_uses_repo_fallback(self, tmp_path: Path) -> None:
        """Dangling symlink is replaced by a copy from repo_fallback."""
        fallback = tmp_path / "fallback.md"
        fallback.write_text("fallback content")

        link = tmp_path / "CLAUDE.md"
        link.symlink_to(tmp_path / "gone.md")  # dangling
        assert not link.exists()

        result = ensure_real_file(link, repo_fallback=fallback)

        assert result is True
        assert not link.is_symlink()
        assert link.is_file()
        assert link.read_text() == "fallback content"

    def test_symlink_with_missing_target_no_fallback_unlinks_returns_false(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dangling symlink with no usable fallback is removed; returns False; warning logged."""
        link = tmp_path / "CLAUDE.md"
        link.symlink_to(tmp_path / "gone.md")  # dangling

        with caplog.at_level(logging.WARNING, logger="mesy"):
            result = ensure_real_file(link, repo_fallback=None)

        assert result is False
        assert not link.exists()
        assert not link.is_symlink()
        assert any("dangling symlink" in r.message for r in caplog.records)


class TestEnsureRealDir:
    def test_real_dir_is_noop_returns_false(self, tmp_path: Path) -> None:
        """A real directory is left untouched and returns False."""
        d = tmp_path / "memory"
        d.mkdir()
        (d / "notes.md").write_text("content")

        result = ensure_real_dir(d, repo_fallback=None)

        assert result is False
        assert d.is_dir()
        assert not d.is_symlink()

    def test_absent_path_is_noop_returns_false(self, tmp_path: Path) -> None:
        """A path that does not exist returns False without raising."""
        d = tmp_path / "memory"

        result = ensure_real_dir(d, repo_fallback=None)

        assert result is False
        assert not d.exists()

    def test_symlink_to_existing_dir_materialises_files(self, tmp_path: Path) -> None:
        """Symlink to a real directory is replaced with a real dir; files are copied."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        (canonical_dir / "notes.md").write_text("# Notes")
        (canonical_dir / "todo.md").write_text("# Todo")

        link = tmp_path / "memory"
        link.symlink_to(canonical_dir)
        assert link.is_symlink()

        result = ensure_real_dir(link, repo_fallback=None)

        assert result is True
        assert not link.is_symlink()
        assert link.is_dir()
        assert (link / "notes.md").read_text() == "# Notes"
        assert (link / "todo.md").read_text() == "# Todo"

    def test_symlink_with_existing_dir_does_not_recurse_into_subdirs(
        self, tmp_path: Path
    ) -> None:
        """Subdirectories inside the target dir are not copied."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        (canonical_dir / "top.md").write_text("# Top")
        subdir = canonical_dir / "sub"
        subdir.mkdir()
        (subdir / "nested.md").write_text("# Nested")

        link = tmp_path / "memory"
        link.symlink_to(canonical_dir)

        ensure_real_dir(link, repo_fallback=None)

        assert (link / "top.md").exists()
        assert not (link / "sub").exists()

    def test_dangling_symlink_uses_fallback_dir(self, tmp_path: Path) -> None:
        """Dangling symlink is replaced by a copy from the repo_fallback directory."""
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "restored.md").write_text("restored")

        link = tmp_path / "memory"
        link.symlink_to(tmp_path / "gone")  # dangling

        result = ensure_real_dir(link, repo_fallback=fallback)

        assert result is True
        assert not link.is_symlink()
        assert link.is_dir()
        assert (link / "restored.md").read_text() == "restored"

    def test_dangling_symlink_no_fallback_unlinks_returns_false(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dangling symlink with no fallback is removed; returns False; warning logged."""
        link = tmp_path / "memory"
        link.symlink_to(tmp_path / "gone")  # dangling

        with caplog.at_level(logging.WARNING, logger="mesy"):
            result = ensure_real_dir(link, repo_fallback=None)

        assert result is False
        assert not link.exists()
        assert not link.is_symlink()
        assert any("dangling symlink" in r.message for r in caplog.records)

    def test_atomic_contract_symlink_intact_on_mid_copy_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If copy2 raises mid-copy the original symlink must remain (atomic guarantee).

        Note: ensure_real_dir's tmp+rename strategy means:
          - tmp dir is created and partially filled
          - symlink.unlink() has NOT happened yet when copy2 raises (the unlink
            is deferred until after the tmp dir is fully populated)
        The symlink itself therefore remains intact.

        Implementation detail: shutil.copy2 is monkeypatched to raise after the
        first file so we simulate a mid-copy crash.
        """
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        (canonical_dir / "a.md").write_text("file A")
        (canonical_dir / "b.md").write_text("file B")

        link = tmp_path / "memory"
        link.symlink_to(canonical_dir)

        call_count = [0]
        original_copy2 = shutil.copy2

        def failing_copy2(src: object, dst: object, **kwargs: object) -> None:
            call_count[0] += 1
            if call_count[0] > 1:
                raise OSError("Simulated crash mid-copy")
            original_copy2(src, dst, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("src.targets._migration.shutil.copy2", failing_copy2)

        with pytest.raises(OSError, match="Simulated crash"):
            ensure_real_dir(link, repo_fallback=None)

        # Symlink must still exist — no partial real-dir left in its place.
        assert link.is_symlink(), (
            "Symlink was removed before the copy completed — atomic contract violated"
        )
