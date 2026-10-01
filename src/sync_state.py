"""Three-way sync state: track content hashes per file to distinguish updates from conflicts."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("mesy")


class SyncState:
    """Persist content hashes so three-way sync can tell who changed a file."""

    def __init__(self, hash_file_path: Path) -> None:
        self._hash_file_path = hash_file_path
        self._hashes: dict[str, str] = {}

        self.load()

    def load(self) -> None:
        if self._hash_file_path.exists():
            try:
                self._hashes = json.loads(self._hash_file_path.read_text())

            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Could not load sync hashes from %s: %s", self._hash_file_path, exc)
                self._hashes = {}

    def save(self) -> None:
        try:
            self._hash_file_path.write_text(json.dumps(self._hashes, indent=2))

        except OSError as exc:
            log.warning("Could not save sync hashes to %s: %s", self._hash_file_path, exc)

    def compute_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def get_hash(self, filepath: str) -> str | None:
        return self._hashes.get(filepath)

    def set_hash(self, filepath: str, content: str) -> None:
        self._hashes[filepath] = self.compute_hash(content)
        self.save()
