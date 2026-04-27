# Three-Phase Refactor: Migration / Adoption / Sync

## Goal

Separate the three concerns currently tangled inside `ClaudeCodeTarget.scan()` and `GenericTarget.scan()` into explicit, sequential phases. Each phase has a single responsibility; mental model maps one-to-one to code structure.

## The Three Phases

```
scan()
├── PHASE 1: migrate()   # reconcile cross-run drift — idempotent housekeeping
├── PHASE 2: adopt()     # classify inputs, build (local, repo) pair list, shape-fix mapped items
└── PHASE 3: sync()      # reconcile content per sync_mode; finalize shape for just-adopted pairs
```

### Phase boundary rules

| Concern | Phase |
|---|---|
| Rename raw-named repo dirs to canonical | Phase 1 (repo-side, pre-walk) |
| Fix local filesystem shape that contradicts current `sync_mode` because mode changed between runs (mapped projects only) | Phase 2 (during source walk) |
| Walk source and classify into mapped / unmapped | Phase 2 |
| Flag unmapped projects with local-side `.needs_mapping` marker — **no repo writes** | Phase 2 |
| Copy new content source ↔ repo | Phase 3 |
| Merge conflicting content | Phase 3 |
| Finalize shape for a just-adopted pair (e.g. merge → repo → symlink local) | Phase 3 |
| Three-way hash reconciliation | Phase 3 |

**Key distinctions:**
- Shape-writes motivated by "prior run used a different mode" happen during Phase 2's walk via `ensure_real_*` helpers (before the pair is yielded). This keeps Phase 3 free of mode-switch leftovers.
- Shape-writes motivated by "this pair is new/changed, finalize per current `sync_mode`" belong to Phase 3.

### Unmapped projects: flag only, no repo touch

Under the new model, unmapped projects get a local-side `.needs_mapping` marker file at `~/.claude/projects/<raw>/.needs_mapping` and **nothing else happens**. No `.pending/<raw>/` dir in the repo, no file copies, no symlinks. Once the user adds a mapping in `projects.conf`, the next scan treats the project as mapped and runs the full flow.

**Consequence:** a user on device A creating a new unmapped project will **not** have that data visible on device B until the mapping is added on A and committed. Confirmed intentional.

This eliminates `_promote_pending`, pending→canonical copy, empty `.pending/` cleanup, and the entire `.pending/` subtree from the design.

### Phase boundary is semantic, not temporal

Phase 1 and Phase 2 **may share a single source walk**. The phase boundary is about *what the code does*, not *when it runs*. Phase 3 remains strictly sequential after the walk completes — no content sync during classification/shape-fix.

```
scan():
    _migrate_repo_names(mapping)              # Phase 1, repo-side only
    pairs = _walk_and_adopt(mapping)          # Phase 2: shape-fix mapped, flag unmapped, yield pairs
    _assert_phase1_invariants(mapping)        # sanity check: no raw names remain in repo
    _sync(pairs)                              # Phase 3: content reconciliation
```

### Clean filesystem postcondition (Phase 3 invariant)

After Phases 1+2, Phase 3 can assume:

- Local paths for mapped projects exist in the shape `sync_mode` expects (real file/dir in copy mode, symlink in symlink mode — for already-adopted pairs)
- Repo uses canonical names; no raw-named dirs
- No dangling symlinks from previous modes
- Unmapped projects do not appear in `pairs` — Phase 3 never sees them

Phase 3 never contains `if path.is_symlink()` branches that handle mode-switch leftovers. If such a branch fires, it's a Phase 2 bug.

## Service Flow — `ClaudeCodeTarget`

### Phase 1: `_migrate_repo_names(mapping)`
- Rename raw → canonical in `repo/claude/projects/` when a mapping exists and the canonical dir is absent.

Note: `ClaudeCodeTarget` does not currently use `SyncState`, so mode-change hash invalidation (see `GenericTarget` Phase 1) is not needed here. If `ClaudeCodeTarget` ever adopts copy-mode three-way sync, the same invalidation logic must be added.

### Phase 2: `_walk_and_adopt(mapping) -> list[ProjectPair]`
1. Iterate `~/.claude/projects/*/`.
2. For each project:
   - **Mapped:** use `ensure_real_file` / `ensure_real_dir` to fix any cross-mode shape drift (symlink→real in copy mode, or record real→symlink intent for Phase 3 finalization). Yield a `ProjectPair`.
   - **Unmapped:** write `~/.claude/projects/<raw>/.needs_mapping` marker. Do **not** yield a pair. Do **not** write to repo.
3. Return `ProjectPair` list (mapped only).

### Phase 3: `_sync(pairs)`
1. Sync global `CLAUDE.md` (local ↔ repo).
2. For each pair:
   - Sync `CLAUDE.md` (file-level).
   - Sync `memory/` (dir-level).
3. Mode dispatch: `_sync_as_symlink` / `_sync_as_copy`. No symlink-migration branches.
4. `_verify_migration` gates the destructive `rmtree(memory_dir)` before creating the final symlink in symlink mode (preserved from current code, stays adjacent to the op it protects).

### Phase 1 invariants (assertion at boundary)

`_assert_phase1_invariants(mapping)` checks:
- No raw name from `mapping` appears as a directory under `repo/claude/projects/`.
- (Optional) every repo project dir name is either canonical-for-some-raw or an unknown name (flagged, not asserted).

Runs after the walk, before Phase 3. Raises on violation.

## Service Flow — `GenericTarget`

### Phase 1: `_migrate()`
- Detect `sync_mode` change since last run by comparing against a persisted `last_sync_mode` value (stored alongside `SyncState`, e.g. in `.mesy_sync_hashes.json` or a sibling file).
- On **entering `copy` mode when the previously-recorded mode differs from `copy`**: invalidate `SyncState` hashes for this target's keys. They are stale relative to any edits made while in the non-copy mode. The current run then re-seeds via first-sync logic (source wins).
- Note: when entering symlink mode, `_sync_state` is `None` so `_migrate()` returns early without persisting `last_sync_mode = "symlink"`. The recorded mode therefore retains its prior value; hash invalidation fires on the subsequent re-entry into copy mode (when the recorded mode still reflects the pre-symlink copy run).
- Persist current `sync_mode` for next run's comparison.
- No per-file shape work here — shape fixes on individual source files happen in Phase 2 via `ensure_real_*` during the walk.

**Note:** current code does not detect mode changes and can produce wrong three-way sync decisions after a copy→symlink→copy round-trip. This Phase 1 is new behavior introduced by the refactor, not a preservation of existing behavior.

### Phase 2: `_walk_and_adopt() -> list[FilePair]`
- Recursive walk of `source` + `repo_dir`.
- For each matching file:
  - Use `ensure_real_file` to fix cross-mode shape drift on the source side (symlink→real in copy mode).
  - Build `(source_path, repo_path)` pair.
- Create empty dirs as needed.
- No content writes beyond the shape-fix itself.

### Phase 3: `_sync(pairs)`
- For each pair:
  - Copy mode → `_three_way_sync` (via `SyncState` hash comparison).
  - Symlink mode → merge-if-differs → symlinkify local → repo.
- Single-cycle convergence.

## Code Structure

```python
class ClaudeCodeTarget(SyncTarget):

    def scan(self) -> None:
        mapping = load_project_mappings(data_dir=self.data_dir)
        self._migrate_repo_names(mapping)
        pairs = self._walk_and_adopt(mapping)
        self._assert_phase1_invariants(mapping)
        self._sync(pairs)

    # ========================================================
    # PHASE 1: MIGRATION (repo-side)
    # Bring repo dir names to canonical shape. Idempotent.
    # ========================================================

    def _migrate_repo_names(self, mapping): ...
    def _assert_phase1_invariants(self, mapping): ...

    # ========================================================
    # PHASE 2: ADOPTION
    # Walk source, fix cross-mode shape drift for mapped projects,
    # flag unmapped with local marker, yield (local, repo) pairs.
    # ========================================================

    def _walk_and_adopt(self, mapping) -> list[ProjectPair]: ...
    def _classify_project(self, project_dir, mapping) -> ProjectPair | None: ...
    def _flag_unmapped(self, project_dir) -> None: ...

    # ========================================================
    # PHASE 3: SYNC
    # Reconcile content and finalize shape. Assumes clean state.
    # ========================================================

    def _sync(self, pairs): ...
    def _sync_global_claude_md(self): ...
    def _sync_project(self, pair): ...
    def _sync_file(self, local, repo, label): ...
    def _sync_dir(self, local, repo, label): ...
```

`GenericTarget` mirrors the same section structure (Phase 1 empty, Phase 2 yields `FilePair`, Phase 3 syncs).

### New types

```python
@dataclass
class ProjectPair:
    local_memory: Path
    local_claude_md: Path
    repo_dir: Path

@dataclass
class FilePair:
    source_file: Path
    repo_file: Path
```

Note: `ProjectPair` no longer has `is_pending` — unmapped projects don't become pairs.

### Shared migration helpers (new `src/targets/_migration.py`)

```python
def ensure_real_file(path: Path, repo_fallback: Path | None) -> bool:
    """Convert path to a real file if it's a symlink. Idempotent.

    Preconditions: path may be symlink, real file, or missing.
    Postcondition: path is either a real file or missing (never a symlink).

    Behavior:
      - path is real file or missing: no-op, return False.
      - path is symlink, target exists: copy target content → replace symlink
        with real file (copy2 for mtime). Return True.
      - path is symlink, target missing, repo_fallback exists: copy fallback
        → real file at path. Return True.
      - path is symlink, target missing, fallback missing/None: unlink, log
        warning, return False. Caller handles absence.

    Never merges content. Never writes to repo. Pure local shape fix.
    Content reconciliation (conflict merge) is Phase 3's job.
    """

def ensure_real_dir(path: Path, repo_fallback: Path | None) -> bool:
    """Convert path to a real directory if it's a symlink. Idempotent.

    Preconditions: path may be symlink-to-dir, real dir, or missing.
    Postcondition: path is real dir or missing.

    Behavior:
      - real dir or missing: no-op, return False.
      - symlink, target is dir that exists: mkdir path.tmp, copy2 each
        non-dir file from target into path.tmp, unlink symlink, rename
        tmp → path. Return True.
      - symlink, target missing, repo_fallback is dir: same copy from
        fallback. Return True.
      - symlink, target missing, fallback missing/None: unlink, warn,
        return False.

    Files only — does not recurse into subdirectories. Matches current
    _copy_dir_files semantics. Never merges conflicts (Phase 3's job).
    Atomic via tmp+rename so a crash mid-copy leaves the symlink intact.
    """
```

Used by Phase 2 in both `ClaudeCodeTarget` and `GenericTarget`. Deduplicates symlink→real logic currently duplicated across `_sync_file_copy`, `_scan_projects` block at 225, and `_adopt_directory_copy`.

### Hash key ownership (GenericTarget copy mode)

**Decision: Option B — Phase 3 recomputes the key inside `_three_way_sync`** via `self._repo_key(pair.repo_file)`. Matches current behavior, minimal refactoring, `_repo_key` is pure path math (no I/O).

**Future consideration (Option A):** store `hash_key` on `FilePair` at construction time. Would make `FilePair` self-contained and `_three_way_sync` testable without a target instance. Defer until there's a concrete testability need.

## Before / After Mapping

| Current | New |
|---|---|
| `_sync_file_copy` does migration + sync | `_sync_file` does sync only; migration via `ensure_real_file` during Phase 2 walk |
| `_scan_projects` does migrate + adopt + sync | Split: shape-fix + classify in Phase 2, content sync in Phase 3 |
| `_promote_pending` does migrate + sync | **Deleted.** No `.pending/` subtree. Unmapped projects only get local-side `.needs_mapping` marker |
| `_sync_global_claude_md` does migrate + sync | Migration via `ensure_real_file` in Phase 2; sync in Phase 3 |
| `GenericTarget._adopt_directory_copy` does migrate + adopt + sync | Split across Phase 2 (walk + shape-fix) and Phase 3 (content) |
| `GenericTarget._adopt_directory` does adopt + sync | Split across Phase 2 and Phase 3 |
| `_migrate_memory_to_dir`, `_copy_dir_files` | Subsumed by `ensure_real_dir` + Phase 3 sync logic |

## Execution Plan

1. Create `src/targets/_migration.py` with `ensure_real_file`, `ensure_real_dir`.
2. Add `ProjectPair`, `FilePair` dataclasses.
3. Refactor `ClaudeCodeTarget` into the new structure (repo-rename Phase 1, combined walk+shape-fix Phase 2, content-only Phase 3). Keep tests green at every step.
4. Refactor `GenericTarget` similarly using shared helpers. Keep tests green at every step.
5. Optionally extend `SyncTarget` ABC to document the phase contract (keep `scan()` as sole public method).
6. After refactor is green, add phase-level tests alongside existing behavioral tests.
7. Update `CLAUDE.md` architecture section to document the three-phase model as project-wide invariant for all future targets.

### Test strategy: green throughout

- All existing behavioral tests (end-to-end `scan()` assertions on filesystem state) remain green at every step of the refactor.
- Internal-method tests that target methods being removed (`_scan_projects`, `_promote_pending`, `_sync_file_copy`, `_migrate_repo_to_canonical`, `_migrate_memory_to_dir`, `_adopt_directory`, `_adopt_directory_copy`) will become redundant. Refer to @refactor_test_deletions.md for the complete list. 
- **Before deleting any test, enumerate it in this doc and get user confirmation.** Pending list of deletion candidates to be added here before refactor begins.
- After refactor is green, add new phase-level tests (per phase × target).

## Project-Wide Invariant

All `SyncTarget` implementations must follow the three-phase model. New targets added in the future must declare their Phase 1 / Phase 2 / Phase 3 sections explicitly. Phase boundary rules (table above) are architectural law, not per-target preference.

Unmapped inputs are never written to the repo. Flag locally, wait for user action.
