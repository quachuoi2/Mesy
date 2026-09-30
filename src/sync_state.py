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

    def set_raw(self, key: str, value: str) -> None:
        """Store *value* verbatim (not hashed) under *key*.

        Used for metadata keys such as ``__last_sync_mode__`` that must be
        compared as plain strings rather than content hashes.
        """
        self._hashes[key] = value
        self.save()

    def get_raw(self, key: str) -> str | None:
        return self._hashes.get(key)

    def clear_target_hashes(self, exclude_keys: set[str] | None = None) -> None:
        """Remove all content hashes except those in *exclude_keys*.

        Called by GenericTarget Phase 1 when a sync_mode change is detected,
        to discard hashes that are stale relative to edits made under the
        previous mode.  The ``__last_sync_mode__`` metadata key is always
        excluded so the mode-tracking record survives the clear.
        """
        skip = exclude_keys or set()
        stale = [k for k in self._hashes if k not in skip]

        for k in stale:
            del self._hashes[k]

        if stale:
            self.save()
            log.info("SyncState: cleared %d stale hash(es)", len(stale))
