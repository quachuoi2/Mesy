"""Verified, recoverable adoption of files and directories into managed links."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path


class LinkConflict(RuntimeError):
    """Both locations contain different versions; neither may be discarded."""


def atomic_write(path: Path, content: bytes) -> None:
    """Replace one regular file atomically, retaining existing permissions."""
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mesy-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def inventory(path: Path) -> dict[str, tuple[str, int]]:
    """Hash regular files, modes, and empty directories without following links."""
    result = {}
    if path.is_symlink():
        raise ValueError(f"Nested symlinks are not adopted: {path}")
    if not path.exists():
        return result
    entries = [path] if path.is_file() else [path, *sorted(path.rglob("*"))]
    for entry in entries:
        if entry.is_symlink():
            raise ValueError(f"Nested symlinks are not adopted: {entry}")
        key = "." if entry == path else entry.relative_to(path).as_posix()
        if entry.is_dir():
            result[key + "/"] = ("directory", 0)
        elif entry.is_file():
            # Git preserves executability, not all host-specific permission bits.
            result[key] = (hashlib.sha256(entry.read_bytes()).hexdigest(), bool(entry.stat().st_mode & 0o111))
        else:
            raise ValueError(f"Only regular files and directories can be adopted: {entry}")
    return result


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def checked_destination(root: Path, path: Path) -> Path:
    """Reject traversal and symlinks in every component below the data root."""
    root = root.resolve()
    path = Path(os.path.abspath(path))
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Managed destination contains a symlink: {current}")
    return path


class LinkManager:
    def __init__(self, data_dir: Path, state_dir: Path):
        self.data_dir = data_dir.resolve()
        self.state_dir = checked_destination(self.data_dir, state_dir)

    def _baseline_path(self, source: Path) -> Path:
        name = hashlib.sha256(str(source).encode()).hexdigest()
        return self.state_dir / "links" / (name + ".json")

    def _remember(self, source: Path, destination: Path) -> None:
        if destination.is_dir():
            return  # Only file-link replacement needs a content baseline.
        path = self._baseline_path(source)
        content = json.dumps(inventory(destination), sort_keys=True).encode()
        if not path.exists() or path.read_bytes() != content:
            atomic_write(path, content)

    def adopt(self, source: Path, destination: Path, directory: bool = False) -> bool:
        """Adopt one location. Preserve divergent versions for explicit resolution.

        A matching file that replaces a managed symlink is safely re-linked.
        Differing replacements are never guessed to be newer than the repo.
        """
        checked_destination(self.data_dir, self.state_dir)
        destination = checked_destination(self.data_dir, destination)
        source = Path(os.path.abspath(source))
        if destination.exists() and destination.is_dir() != directory:
            raise ValueError(f"Unexpected destination type: {destination}")
        if source.is_symlink() and source.resolve() == destination and destination.exists():
            self._remember(source, destination)
            return False
        original_link = os.readlink(source) if source.is_symlink() else None
        original = source.resolve() if original_link is not None else source
        if original.exists() and original.is_dir() != directory:
            raise ValueError(f"Unexpected file type: {source}")
        before = inventory(original)
        remote = inventory(destination)
        original_existed = original.exists()
        baseline_path = self._baseline_path(source)
        # An atomic save can replace a file link. Accept it only when the
        # repository still matches the last observed linked version.
        if not directory and original_link is None and before and remote and before != remote and baseline_path.exists():
            try:
                baseline = {k: tuple(v) for k, v in json.loads(baseline_path.read_text()).items()}
            except (OSError, ValueError, TypeError, AttributeError):
                baseline = None
            if baseline == remote:
                backup = self.state_dir / "backups" / uuid.uuid4().hex
                backup.mkdir(parents=True)
                shutil.copy2(destination, backup / "repository")
                # Recheck immediately before publication, preserving both on a race.
                if inventory(original) != before or inventory(destination) != remote:
                    raise RuntimeError(f"Content changed during link repair: {source}")
                atomic_write(destination, original.read_bytes())
                shutil.copymode(original, destination)
                remote = inventory(destination)
        # Empty directory roots are compatible; overlapping different files are not.
        differences = [key for key in before.keys() & remote.keys() if before[key] != remote[key]]
        # File versus directory collisions must also be caught before any copying.
        if any(key.rstrip("/") in {k.rstrip("/") for k in remote}
               and key not in remote for key in before):
            differences.append("file/directory collision")
        if differences:
            fingerprint = hashlib.sha256(json.dumps([str(source), str(destination), before, remote], sort_keys=True).encode()).hexdigest()[:20]
            conflict = self.state_dir / "conflicts" / fingerprint
            if not conflict.exists():
                conflict.mkdir(parents=True)
                copy_path(original, conflict / "local")
                copy_path(destination, conflict / "repository")
                atomic_write(conflict / "paths.json", json.dumps({"source": str(source), "destination": str(destination)}, indent=2).encode())
            raise LinkConflict(f"Divergent files at {source}; versions saved in {conflict}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        source.parent.mkdir(parents=True, exist_ok=True)
        # Probe link support before moving any source content (notably on Windows).
        probe = source.parent / (".mesy-link-" + uuid.uuid4().hex)
        try:
            probe.symlink_to(destination, target_is_directory=directory)
        finally:
            if probe.is_symlink():
                probe.unlink()

        backup = self.state_dir / "backups" / uuid.uuid4().hex
        if original.exists():
            backup.mkdir(parents=True)
            copy_path(original, backup / "content")
            atomic_write(backup / "paths.json", json.dumps({"source": str(source), "destination": str(destination), "original_link": original_link}, indent=2).encode())
        if directory:
            destination.mkdir(parents=True, exist_ok=True)
            if original.exists():
                for item in sorted(original.rglob("*")):
                    dest = destination / item.relative_to(original)
                    if item.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    elif not dest.exists():
                        shutil.copy2(item, dest)
        elif original.exists() and not destination.exists():
            shutil.copy2(original, destination)
        elif not destination.exists():
            atomic_write(destination, b"")
        after = inventory(destination)
        if (original_existed and inventory(original) != before) or any(after.get(k) != v for k, v in before.items()):
            raise RuntimeError(f"Content changed during adoption; source retained: {source}")

        # Rename the original rather than deleting it. Restore it if link creation fails.
        saved = source.parent / (".mesy-original-" + uuid.uuid4().hex)
        existed = source.exists() or source.is_symlink()
        if existed:
            source.rename(saved)
        try:
            source.symlink_to(destination, target_is_directory=directory)
        except OSError:
            if existed:
                saved.rename(source)
            raise
        if existed:
            # Keep the exact displaced object for crash recovery and rollback.
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(saved), str(backup / "displaced"))
        self._remember(source, destination)
        return True

    def materialize(self, source: Path, destination: Path) -> bool:
        """Detach a managed link, retaining the latest synced content locally."""
        destination = checked_destination(self.data_dir, destination)
        if not source.is_symlink() or source.resolve() != destination:
            return False
        before = inventory(destination)
        if not destination.exists():
            raise ValueError(f"Cannot detach a broken link: {source}")
        temporary = source.parent / (".mesy-detach-" + uuid.uuid4().hex)
        copy_path(destination, temporary)
        if inventory(temporary) != before or inventory(destination) != before:
            raise RuntimeError(f"Content changed during detach; link retained: {source}")
        source.unlink()
        try:
            temporary.rename(source)
        except OSError:
            source.symlink_to(destination, target_is_directory=destination.is_dir())
            raise
        return True
