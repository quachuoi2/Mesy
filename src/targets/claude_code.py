"""Claude Code sync target — project mapping, pending adoption, CLAUDE.md sync."""

import filecmp
import logging
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_DIR, load_project_mappings
from src.git_utils import merge_conflicting_file
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
PENDING_DIR = REPO_DIR / "projects" / ".pending"


class ClaudeCodeTarget(SyncTarget):

    def __init__(
        self,
        source: Path,
        repo_dir: Path,
        claude_dir: Path | None = None,
        projects_dir: Path | None = None,
        pending_dir: Path | None = None,
        repo_base: Path | None = None,
        **kwargs,
    ):
        super().__init__(
            name="claude-code",
            source=source,
            repo_dir=repo_dir,
            file_patterns=["*.md"],
        )
        self.claude_dir = claude_dir or CLAUDE_DIR
        self.projects_dir = projects_dir or PROJECTS_DIR
        self.repo_base = repo_base or REPO_DIR
        self.pending_dir = pending_dir or (self.repo_base / "projects" / ".pending")

    def setup(self) -> None:
        """Initial migration — called by setup.py, not the daemon."""
        pass

    def scan(self) -> None:
        """Discover projects, adopt mapped ones, stage unmapped in .pending."""
        self._sync_global_claude_md()

        if not self.projects_dir.exists():
            return

        mapping = load_project_mappings()
        self._migrate_repo_to_canonical(mapping)
        self._promote_pending(mapping)
        self._scan_projects(mapping)

    def get_stage_dirs(self) -> list[str]:
        return ["global/", "projects/"]

    def get_watched_paths(self) -> list[Path]:
        paths = [self.repo_dir, self.repo_base / "global"]
        if self.projects_dir.exists():
            paths.append(self.projects_dir)
        return paths

    # -- internal methods (moved from sync.py) --

    def _sync_global_claude_md(self) -> None:
        """Migrate and symlink the global ~/.claude/CLAUDE.md."""
        local_claude = self.claude_dir / "CLAUDE.md"
        repo_claude = self.repo_base / "global" / "CLAUDE.md"

        if local_claude.is_symlink():
            return

        if local_claude.is_file():
            if not repo_claude.exists():
                shutil.copy2(local_claude, repo_claude)
                log.info("Copied global CLAUDE.md into repo")
            elif not filecmp.cmp(local_claude, repo_claude, shallow=False):
                merge_conflicting_file(local=local_claude, repo=repo_claude)
            local_claude.unlink()
            local_claude.symlink_to(repo_claude)
            log.info("Symlinked global CLAUDE.md")
        elif repo_claude.exists() and not local_claude.exists():
            local_claude.symlink_to(repo_claude)
            log.info("Symlinked global CLAUDE.md (from repo)")

    def _sync_claude_md(self, project_dir: Path, repo_dir: Path) -> None:
        """Migrate and symlink a project's CLAUDE.md file."""
        local_claude = project_dir / "CLAUDE.md"
        repo_claude = repo_dir / "CLAUDE.md"

        if local_claude.is_symlink():
            return

        if local_claude.is_file():
            repo_dir.mkdir(parents=True, exist_ok=True)
            if not repo_claude.exists():
                shutil.copy2(local_claude, repo_claude)
                log.info("Copied CLAUDE.md for %s into repo", project_dir.name)
            elif not filecmp.cmp(local_claude, repo_claude, shallow=False):
                merge_conflicting_file(local=local_claude, repo=repo_claude)
            local_claude.unlink()
            local_claude.symlink_to(repo_claude)
            log.info("Symlinked CLAUDE.md for %s", project_dir.name)
        elif repo_claude.exists():
            local_claude.symlink_to(repo_claude)
            log.info("Symlinked CLAUDE.md for %s (from repo)", project_dir.name)

    def _migrate_repo_to_canonical(self, mapping: dict[str, str]) -> None:
        """Rename raw-named project dirs in the repo to their canonical names."""
        projects_dir = self.repo_base / "projects"
        if not projects_dir.exists():
            return

        canonical_raws: dict[str, list[str]] = {}
        for raw, canonical in mapping.items():
            canonical_raws.setdefault(canonical, []).append(raw)

        for canonical, raw_names in canonical_raws.items():
            canonical_dir = projects_dir / canonical
            if canonical_dir.exists():
                continue
            for raw_name in raw_names:
                raw_dir = projects_dir / raw_name
                if raw_dir.exists() and raw_dir.is_dir():
                    raw_dir.rename(canonical_dir)
                    log.info("Renamed repo dir: %s -> %s", raw_name, canonical)
                    break

    def _migrate_memory_to_dir(self, memory_dir: Path, target_dir: Path) -> None:
        """Copy/merge files from a memory directory into a target repo directory."""
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in memory_dir.iterdir():
            if f.is_dir():
                continue
            dest = target_dir / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
            elif not filecmp.cmp(f, dest, shallow=False):
                merge_conflicting_file(local=f, repo=dest)

    def _promote_pending(self, mapping: dict[str, str]) -> None:
        """Move pending projects to canonical dirs when a mapping is added."""
        if not self.pending_dir.exists():
            return

        for pending_dir in list(self.pending_dir.iterdir()):
            if not pending_dir.is_dir():
                continue
            raw_name = pending_dir.name
            if raw_name not in mapping:
                continue

            canonical = mapping[raw_name]
            canonical_dir = self.repo_base / "projects" / canonical
            canonical_dir.mkdir(parents=True, exist_ok=True)

            for f in pending_dir.iterdir():
                if f.name == ".needs_mapping" or f.is_dir():
                    continue
                dest = canonical_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                elif not filecmp.cmp(f, dest, shallow=False):
                    merge_conflicting_file(local=f, repo=dest)

            all_migrated = all(
                (canonical_dir / f.name).exists()
                for f in pending_dir.iterdir()
                if not f.is_dir() and f.name != ".needs_mapping"
            )
            if not all_migrated:
                log.error("Migration verification failed for pending %s, skipping rmtree", raw_name)
                continue

            shutil.rmtree(pending_dir)
            log.info("Promoted pending project %s -> %s", raw_name, canonical)

            claude_memory = self.projects_dir / raw_name / "memory"
            if claude_memory.exists() and not claude_memory.is_symlink():
                shutil.rmtree(claude_memory)
                claude_memory.symlink_to(canonical_dir)
                log.info("Symlinked %s -> %s", raw_name, canonical)

        if self.pending_dir.exists() and not any(self.pending_dir.iterdir()):
            self.pending_dir.rmdir()

    def _scan_projects(self, mapping: dict[str, str]) -> None:
        """Scan Claude Code projects and adopt/stage them."""
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            memory_dir = project_dir / "memory"
            if not memory_dir.exists():
                continue
            if memory_dir.is_symlink():
                raw_name = project_dir.name
                if raw_name in mapping:
                    canonical_dir = self.repo_base / "projects" / mapping[raw_name]
                    self._sync_claude_md(project_dir, canonical_dir)
                continue

            raw_name = project_dir.name

            if raw_name in mapping:
                canonical = mapping[raw_name]
                canonical_dir = self.repo_base / "projects" / canonical
                self._migrate_memory_to_dir(memory_dir, canonical_dir)

                # Verify all files migrated before deleting
                if not self._verify_migration(memory_dir, canonical_dir):
                    log.error("Migration verification failed for %s, skipping rmtree", raw_name)
                    continue
                shutil.rmtree(memory_dir)
                memory_dir.symlink_to(canonical_dir)
                log.info("Adopted project memory: %s -> %s", raw_name, canonical)

                self._sync_claude_md(project_dir, canonical_dir)
            else:
                pending_dest = self.pending_dir / raw_name
                if not pending_dest.exists():
                    self.pending_dir.mkdir(parents=True, exist_ok=True)
                    self._migrate_memory_to_dir(memory_dir, pending_dest)

                    hostname = socket.gethostname()
                    now = datetime.now(timezone.utc).isoformat()
                    marker = pending_dest / ".needs_mapping"
                    marker.write_text(
                        f"source: {hostname}\n"
                        f"discovered: {now}\n"
                        f"raw_name: {raw_name}\n"
                        f"\n"
                        f"Add a mapping in projects.conf:\n"
                        f"  [YourProjectName]\n"
                        f"  paths = {raw_name}\n"
                    )

                    if not self._verify_migration(memory_dir, pending_dest):
                        log.error("Migration verification failed for %s, skipping rmtree", raw_name)
                        continue
                    shutil.rmtree(memory_dir)
                    memory_dir.symlink_to(pending_dest)
                    log.warning(
                        "Unmapped project memory: %s — staged in .pending/. "
                        "Add a mapping in projects.conf to assign a canonical name.",
                        raw_name,
                    )

    def _verify_migration(self, source_dir: Path, target_dir: Path) -> bool:
        """Check that every file in source_dir has a corresponding file in target_dir."""
        for f in source_dir.iterdir():
            if f.is_dir():
                continue
            if not (target_dir / f.name).exists():
                return False
        return True
