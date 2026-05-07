"""Claude Code sync target — project mapping, CLAUDE.md sync, three-phase model."""

from __future__ import annotations

import filecmp
import logging
import shutil
from pathlib import Path

from src.config import load_project_mappings
from src.git_utils import merge_conflicting_file
from src.pending import add_pending, pending_path, prune_pending
from src.targets._migration import ProjectPair, ensure_real_dir, ensure_real_file
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


class ClaudeCodeTarget(SyncTarget):

    def __init__(
        self,
        source: Path,
        repo_dir: Path,
        data_dir: Path,
        claude_dir: Path | None = None,
        projects_dir: Path | None = None,
        repo_base: Path | None = None,
        sync_mode: str = "symlink",
    ):
        super().__init__(
            name="claude-code",
            source=source,
            repo_dir=repo_dir,
            data_dir=data_dir,
            file_patterns=["*.md"],
            sync_mode=sync_mode,
        )
        self.claude_dir = claude_dir or CLAUDE_DIR
        self.projects_dir = projects_dir or PROJECTS_DIR
        self.repo_base = repo_base or self.data_dir

    def setup(self) -> None:
        """Initial migration — called by setup.py, not the daemon."""
        pass

    def scan(self) -> None:
        """Discover projects and sync them using the three-phase model."""
        mapping = load_project_mappings(data_dir=self.data_dir)

        # ------------------------------------------------------------------
        # PHASE 1: MIGRATION (repo-side)
        # Bring repo dir names to canonical shape.  Idempotent.
        # Also drop pending snippets for projects that have since been mapped.
        # ------------------------------------------------------------------
        self._migrate_repo_names(mapping)
        prune_pending(self.repo_base, mapping)

        # ------------------------------------------------------------------
        # PHASE 2: ADOPTION
        # Walk source, fix cross-mode shape drift for mapped projects,
        # record unmapped in repo-side .pending file, yield (local, repo) pairs.
        # ------------------------------------------------------------------
        if not self.projects_dir.exists():
            pairs: list[ProjectPair] = []
        else:
            pairs = self._walk_and_adopt(mapping)

        self._assert_phase1_invariants(mapping)

        # ------------------------------------------------------------------
        # PHASE 3: SYNC
        # Reconcile content and finalise shape.  Assumes clean postcondition
        # from Phases 1+2: no dangling symlinks, no raw-named repo dirs.
        # ------------------------------------------------------------------
        self._sync(pairs)

    def get_stage_dirs(self) -> list[str]:
        return ["claude/global/", "claude/projects/"]

    def get_watched_paths(self) -> list[Path]:
        paths = [self.repo_dir, self.repo_base / "claude" / "global"]
        if self.projects_dir.exists():
            paths.append(self.projects_dir)
        return paths

    # ======================================================================
    # PHASE 1: MIGRATION (repo-side)
    # Bring repo dir names to canonical shape.  Idempotent.
    # ======================================================================

    def _migrate_repo_names(self, mapping: dict[str, str]) -> None:
        """Rename raw-named project dirs in the repo to their canonical names."""
        projects_dir = self.repo_base / "claude" / "projects"
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

    def _assert_phase1_invariants(self, mapping: dict[str, str]) -> None:
        """Assert no raw names from the mapping remain as dirs in the repo.

        Runs after Phase 1 migration, before Phase 3 sync.  Raises
        AssertionError on violation so callers can catch and log.
        """
        projects_dir = self.repo_base / "claude" / "projects"
        if not projects_dir.exists():
            return
        existing = {d.name for d in projects_dir.iterdir() if d.is_dir()}
        for raw in mapping:
            if raw in existing:
                raise AssertionError(
                    f"Phase 1 invariant violated: raw-named dir '{raw}' still exists "
                    f"in repo after migration. Check _migrate_repo_names."
                )

    # ======================================================================
    # PHASE 2: ADOPTION
    # Walk source, fix cross-mode shape drift for mapped projects,
    # record unmapped in repo .pending file, yield (local, repo) pairs.
    # ======================================================================

    def _walk_and_adopt(self, mapping: dict[str, str]) -> list[ProjectPair]:
        """Walk ~/.claude/projects/, classify each, return mapped-only pairs.

        For mapped projects: use ensure_real_* to fix cross-mode shape drift
        (symlink leftover from a prior sync_mode change) before yielding.
        For unmapped projects: append the raw name to the shared repo
        ``.pending`` file and do NOT yield a pair — Phase 3 never sees them.
        """
        pairs: list[ProjectPair] = []
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            pair = self._classify_project(project_dir, mapping)
            if pair is not None:
                pairs.append(pair)
        return pairs

    def _classify_project(
        self, project_dir: Path, mapping: dict[str, str]
    ) -> ProjectPair | None:
        """Classify a single project dir; return a ProjectPair if mapped, else None.

        Applies ensure_real_* shape-fixes for mapped projects in copy mode.
        Records unmapped projects in the repo-side ``.pending`` file.
        """
        raw_name = project_dir.name
        memory_dir = project_dir / "memory"
        local_claude_md = project_dir / "CLAUDE.md"

        # Project must have at least a memory dir (real, symlink, or absent is OK —
        # we still handle it) or a CLAUDE.md to be worth adopting.
        memory_exists = memory_dir.exists() or memory_dir.is_symlink()
        claude_md_exists = local_claude_md.exists() or local_claude_md.is_symlink()
        if not memory_exists and not claude_md_exists:
            return None

        if raw_name in mapping:
            canonical = mapping[raw_name]
            canonical_dir = self.repo_base / "claude" / "projects" / canonical

            if self.sync_mode == "copy":
                # Fix shape: memory_dir must be a real dir (not a symlink) in copy mode.
                if memory_dir.is_symlink():
                    ensure_real_dir(memory_dir, repo_fallback=canonical_dir)
                # Fix shape: CLAUDE.md must be a real file (not a symlink) in copy mode.
                if local_claude_md.is_symlink():
                    ensure_real_file(local_claude_md, repo_fallback=canonical_dir / "CLAUDE.md")

            return ProjectPair(
                local_memory=memory_dir,
                local_claude_md=local_claude_md,
                repo_dir=canonical_dir,
            )

        # Unmapped — record in shared repo .pending file.  No local writes.
        self._flag_unmapped(project_dir)
        return None

    def _flag_unmapped(self, project_dir: Path) -> None:
        """Record an unmapped project in the shared repo ``.pending`` file."""
        raw_name = project_dir.name
        added = add_pending(self.repo_base, raw_name)
        if added:
            log.warning(
                "Unmapped project: %s — recorded in %s. "
                "Add a mapping in projects.conf to sync this project.",
                raw_name,
                pending_path(self.repo_base),
            )

    # ======================================================================
    # PHASE 3: SYNC
    # Reconcile content and finalise shape.  Assumes clean postcondition
    # from Phases 1+2 (no dangling symlinks, no raw-named repo dirs).
    # ======================================================================

    def _sync(self, pairs: list[ProjectPair]) -> None:
        """Sync global CLAUDE.md then each project pair."""
        self._sync_global_claude_md()
        for pair in pairs:
            self._sync_project(pair)

    def _sync_global_claude_md(self) -> None:
        """Sync the global ~/.claude/CLAUDE.md against the repo."""
        local_claude = self.claude_dir / "CLAUDE.md"
        repo_claude = self.repo_base / "claude" / "global" / "CLAUDE.md"
        repo_claude.parent.mkdir(parents=True, exist_ok=True)
        self._sync_file(local_claude, repo_claude, label="global CLAUDE.md")

    def _sync_project(self, pair: ProjectPair) -> None:
        """Sync a single mapped project's CLAUDE.md and memory/ dir."""
        pair.repo_dir.mkdir(parents=True, exist_ok=True)
        self._sync_file(
            pair.local_claude_md,
            pair.repo_dir / "CLAUDE.md",
            label=f"CLAUDE.md for {pair.local_claude_md.parent.name}",
        )
        self._sync_dir(pair.local_memory, pair.repo_dir, label=pair.local_claude_md.parent.name)

    def _sync_file(self, local: Path, repo: Path, label: str) -> None:
        """Reconcile a single file between local and repo per sync_mode.

        Precondition (Phase 2 guarantee): *local* is either a real file,
        absent, or already a correct symlink — never a dangling/wrong symlink.

        symlink mode:
          - Already a symlink → no-op (Phase 2 left it in place or Phase 3
            is re-entering an already-adopted file).
          - local is a real file:
              - repo missing → copy local → repo, symlink local → repo.
              - repo exists, same content → symlink local → repo.
              - repo exists, differs → merge, then symlink local → repo.
          - local absent, repo exists → symlink local → repo.

        copy mode:
          - local exists, repo missing → copy local → repo.
          - repo exists, local missing → copy repo → local.
          - both exist, differ → delegate to merge_conflicting_file.
          - both identical → no-op.
        """
        if self.sync_mode == "copy":
            if local.is_file():
                if not repo.exists():
                    repo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local, repo)
                    log.info("Copied %s into repo (copy mode)", label)
                elif not filecmp.cmp(local, repo, shallow=False):
                    merge_conflicting_file(local=local, repo=repo)
            elif repo.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo, local)
                log.info("Copied %s -> local (copy mode)", label)
            return

        # symlink mode
        if local.is_symlink():
            return

        if local.is_file():
            repo.parent.mkdir(parents=True, exist_ok=True)
            if not repo.exists():
                shutil.copy2(local, repo)
                log.info("Copied %s into repo", label)
            elif not filecmp.cmp(local, repo, shallow=False):
                merge_conflicting_file(local=local, repo=repo)
            local.unlink()
            local.symlink_to(repo)
            log.info("Symlinked %s", label)
        elif repo.exists() and not local.exists():
            local.symlink_to(repo)
            log.info("Symlinked %s (from repo)", label)

    def _sync_dir(self, local_memory: Path, repo_dir: Path, label: str) -> None:
        """Reconcile the memory/ directory between local and repo per sync_mode.

        Precondition: local_memory is a real dir, absent, or correct symlink
        (Phase 2 ensured no wrong-mode symlinks remain for mapped projects).

        symlink mode:
          - Already a symlink pointing at repo_dir → no-op.
          - Real dir with files → copy files to repo_dir, verify, rmtree, symlink.
          - Absent, repo_dir exists → create symlink.

        copy mode:
          - Already a real dir (Phase 2 guarantee) → copy files bidirectionally.
          - Absent → create and sync from repo.
        """
        if self.sync_mode == "copy":
            local_memory.mkdir(parents=True, exist_ok=True)
            repo_dir.mkdir(parents=True, exist_ok=True)
            # Copy local → repo
            for f in local_memory.iterdir():
                if f.is_dir():
                    continue
                dest = repo_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                elif not filecmp.cmp(f, dest, shallow=False):
                    merge_conflicting_file(local=f, repo=dest)
            # Copy repo → local (files only in repo, e.g. from another device)
            for f in repo_dir.iterdir():
                if f.is_dir():
                    continue
                dest = local_memory / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
            return

        # symlink mode
        if local_memory.is_symlink():
            # Already adopted — leave in place (Phase 2 verified it's not a wrong symlink).
            return

        if local_memory.is_dir():
            repo_dir.mkdir(parents=True, exist_ok=True)
            # Copy files local → repo
            for f in local_memory.iterdir():
                if f.is_dir():
                    continue
                dest = repo_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                    log.info("Copied %s for %s into repo", f.name, label)
                elif not filecmp.cmp(f, dest, shallow=False):
                    merge_conflicting_file(local=f, repo=dest)

            if not self._verify_migration(local_memory, repo_dir):
                log.error("Migration verification failed for %s, skipping rmtree", label)
                return

            shutil.rmtree(local_memory)
            local_memory.symlink_to(repo_dir)
            log.info("Adopted project memory: %s -> %s", label, repo_dir.name)
            
        elif repo_dir.exists() and not local_memory.exists():
            local_memory.symlink_to(repo_dir)
            log.info("Symlinked memory for %s (from repo)", label)

    # ======================================================================
    # INTERNAL UTILITIES
    # ======================================================================

    def _verify_migration(self, source_dir: Path, target_dir: Path) -> bool:
        """Check that every file in source_dir has a corresponding file in target_dir."""
        for f in source_dir.iterdir():
            if f.is_dir():
                continue
            if not (target_dir / f.name).exists():
                return False
        return True

