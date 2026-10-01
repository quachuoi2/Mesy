"""Claude Code sync target — project mapping, CLAUDE.md and rules/ sync."""

from __future__ import annotations

import logging
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from src.pending import add_pending, pending_path, prune_pending
from src.sync_modes.base import SyncMode
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


@dataclass
class ProjectPair:
    """A mapped Claude Code project with its local and repo paths."""

    local_memory: Path   # ~/.claude/projects/<raw>/memory
    canonical_dir: Path       # <data_repo>/claude/projects/<canonical>


class ClaudeCodeTarget(SyncTarget):

    def __init__(
        self,
        name: str,
        src: Path,
        target_dir: Path,
        mode: SyncMode,
        repo_base: Path,
        claude_dir: Path | None = None,
        projects_dir: Path | None = None,
    ):
        super().__init__(
            name=name,
            src=src,
            target_dir=target_dir,
            mode=mode,
            repo_base=repo_base,
        )

        self.claude_dir = claude_dir or CLAUDE_DIR
        self.projects_dir = projects_dir or PROJECTS_DIR

    def setup(self) -> None:
        """Initial migration — called by setup.py, not the daemon."""
        pass

    def scan(self) -> None:
        """Discover projects and sync them."""
        mapping = load_project_mappings(repo_base=self.repo_base)

        prune_pending(self.repo_base, mapping)

        if not self.projects_dir.exists():
            pairs: list[ProjectPair] = []
        else:
            pairs = self._walk_and_adopt(mapping)

        self._sync(pairs)

    def get_stage_dirs(self) -> list[str]:
        return ["claude/global/", "claude/projects/"]

    def _walk_and_adopt(self, mapping: dict[str, str]) -> list[ProjectPair]:
        """Walk ~/.claude/projects/, classify each, return mapped-only pairs.

        Unmapped projects are recorded in the shared repo ``.pending`` file
        and never synced.
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
        """Return a ProjectPair if mapped; record unmapped projects in ``.pending``."""
        raw_name = project_dir.name
        memory_dir = project_dir / "memory"

        if not memory_dir.exists() and not memory_dir.is_symlink():
            return None

        if raw_name in mapping:
            return ProjectPair(
                local_memory=memory_dir,
                canonical_dir=self.repo_base / "claude" / "projects" / mapping[raw_name],
            )

        self._flag_unmapped(project_dir)
        return None

    def _flag_unmapped(self, project_dir: Path) -> None:
        """Record an unmapped project in the shared repo ``.pending`` file."""
        raw_name = project_dir.name
        added = add_pending(self.repo_base, raw_name)
        if added:
            log.warning(
                "Unmapped project: %s — recorded in %s. "
                "Add a mapping in projects.ini to sync this project.",
                raw_name,
                pending_path(self.repo_base),
            )

    def _sync(self, pairs: list[ProjectPair]) -> None:
        """Sync global CLAUDE.md and rules/, then each project pair."""
        self._sync_global()

        for pair in pairs:
            self._sync_project(pair)

    def _sync_global(self) -> None:
        """Sync ~/.claude/CLAUDE.md and ~/.claude/rules/ against the repo."""
        global_dir = self.repo_base / "claude" / "global"
        global_dir.mkdir(parents=True, exist_ok=True)

        self.mode.sync_file(self.claude_dir / "CLAUDE.md", global_dir / "CLAUDE.md", label="global CLAUDE.md")
        self.mode.sync_dir(self.claude_dir / "rules", global_dir / "rules", label="global rules")

    def _sync_project(self, pair: ProjectPair) -> None:
        """Sync a single mapped project's memory/ dir."""
        pair.canonical_dir.mkdir(parents=True, exist_ok=True)

        self.mode.sync_dir(pair.local_memory, pair.canonical_dir, label=pair.local_memory.parent.name)


def load_project_mappings(repo_base: Path) -> dict[str, str]:
    """Load projects.ini from repo_base and return {raw_claude_name: canonical_name} mapping."""
    projects_cfg_path = repo_base / "claude" / "projects.ini"
    mapping = {}
    if not projects_cfg_path.exists():
        return mapping

    projects_cfg = ConfigParser()
    projects_cfg.read(projects_cfg_path)

    for section in projects_cfg.sections():
        canonical = section
        paths_str = projects_cfg.get(section, "paths", fallback="")
        for raw_name in paths_str.split(","):
            raw_name = raw_name.strip()
            if raw_name:
                mapping[raw_name] = canonical

    return mapping

