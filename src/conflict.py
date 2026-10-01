"""Conflict merge: combine two versions of a file with ``git merge-file``, marking only overlapping edits."""

import logging
import socket
import tempfile
from pathlib import Path

from src.git_sync import git

MERGE_FILE_MAX_CONFLICTS = 127
log = logging.getLogger("mesy")


def merge_conflicting_file(local: Path, repo: Path, base_text: str = "") -> None:
    """Merge local and repo against base_text and write the result to both.

    Edits that don't overlap are combined; overlapping ones get git's conflict markers.
    An empty base_text (no last synced version) marks every block that differs.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        base = Path(tmp_dir) / "base"
        base.write_text(base_text)
        result = git(
            "merge-file", "-p",
            "-L", f"local ({socket.gethostname()})", "-L", "base", "-L", "repo",
            str(local), str(base), str(repo),
            repo_base=repo.parent,
        )

    # git exits with the number of conflicts (at most 127), or a negative number on error.
    if result.returncode > MERGE_FILE_MAX_CONFLICTS:
        raise RuntimeError(f"git merge-file failed for {repo}: {result.stderr.strip()}")

    repo.write_text(result.stdout)
    local.write_text(result.stdout)

    if result.returncode == 0:
        log.info("Merged %s (edits did not overlap)", repo.name)
    else:
        log.info("Conflict in %s: %d block(s) marked for manual resolution", repo.name, result.returncode)
