"""Tests for src/sync_state.py — hash storage, compute, save/load."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sync_state import SyncState


@pytest.fixture
def state(tmp_path) -> SyncState:
    """Fresh SyncState backed by a temp file."""
    return SyncState(hash_file=tmp_path / ".mesy_sync_hashes.json")


class TestComputeHash:
    def test_returns_hex_string(self, state):
        result = state.compute_hash("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # sha256 hex digest

    def test_same_content_same_hash(self, state):
        assert state.compute_hash("foo") == state.compute_hash("foo")

    def test_different_content_different_hash(self, state):
        assert state.compute_hash("foo") != state.compute_hash("bar")


class TestGetSetHash:
    def test_get_returns_none_for_unknown_key(self, state):
        assert state.get_hash("no/such/file.md") is None

    def test_set_then_get_returns_hash(self, state):
        state.set_hash("a/b.md", "content")
        expected = state.compute_hash("content")
        assert state.get_hash("a/b.md") == expected

    def test_overwrite_updates_hash(self, state):
        state.set_hash("a/b.md", "v1")
        state.set_hash("a/b.md", "v2")
        assert state.get_hash("a/b.md") == state.compute_hash("v2")


class TestPersistence:
    def test_save_creates_file(self, tmp_path):
        path = tmp_path / ".mesy_sync_hashes.json"
        s = SyncState(hash_file=path)
        s.set_hash("x.md", "hello")
        assert path.exists()

    def test_load_restores_state(self, tmp_path):
        path = tmp_path / ".mesy_sync_hashes.json"
        s1 = SyncState(hash_file=path)
        s1.set_hash("docs/notes.md", "some content")

        # New instance reads from same file
        s2 = SyncState(hash_file=path)
        assert s2.get_hash("docs/notes.md") == s1.compute_hash("some content")

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / ".mesy_sync_hashes.json"
        path.write_text("not valid json")
        s = SyncState(hash_file=path)
        assert s.get_hash("anything") is None

    def test_missing_file_starts_empty(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        s = SyncState(hash_file=path)
        assert s.get_hash("x") is None

    def test_set_hash_calls_save(self, tmp_path):
        path = tmp_path / ".mesy_sync_hashes.json"
        s = SyncState(hash_file=path)
        s.set_hash("a.md", "content")
        raw = json.loads(path.read_text())
        assert "a.md" in raw
