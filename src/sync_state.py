"""Three-way sync state: per file, the hash and text of the last synced version."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("mesy")


class SyncState:
    """Persist hashes so three-way sync can tell who changed a file, and texts to merge against."""

    def __init__(self, hash_file_path: Path, text_file_path: Path) -> None:
        self._hash_file_path = hash_file_path
        self._text_file_path = text_file_path
        self._hashes: dict[str, str] = _load_json(self._hash_file_path)
        self._texts: dict[str, str] = _load_json(self._text_file_path)

    def compute_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def get_hash(self, filepath: str) -> str | None:
        return self._hashes.get(filepath)

    def get_text(self, filepath: str) -> str:
        return self._texts.get(filepath, "")

    def set_synced(self, filepath: str, content: str) -> None:
        self._hashes[filepath] = self.compute_hash(content)
        self._texts[filepath] = content

        _save_json(self._hash_file_path, self._hashes)
        _save_json(self._text_file_path, self._texts)


def _load_json(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text())

    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load sync state from %s: %s", path, exc)
        return {}


def _save_json(path: Path, data: dict[str, str]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))

    except OSError as exc:
        log.warning("Could not save sync state to %s: %s", path, exc)
