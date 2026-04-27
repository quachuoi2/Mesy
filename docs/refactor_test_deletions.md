# Three-Phase Refactor: Test Deletion Enumeration

Companion to [refactor_three_phase.md](refactor_three_phase.md). Enumerates every existing test that becomes redundant under the refactor. Deletions require user approval before the refactor starts.

DOUBLE CHECK all the tests before deleting to make sure that nothing becomes untested.
## Summary by category

| Category | Count | Action |
|---|---|---|
| Behavioral tests (call `scan()` / `setup()`, assert filesystem state) | ~43 | **Keep.** Survive refactor unchanged. |
| Internal-method tests calling methods that get renamed/split | ~10 | **Delete.** Enumerated below. |
| Tests for behavior that disappears entirely (`.pending/` staging) | ~4 | **Delete.** Enumerated below. |

Totals:

| | `test_claude_code.py` | `test_generic.py` |
|---|---|---|
| Keep | ~17 | ~24 |
| Delete | 14 | 0 |
| Rewrite post-refactor as unit tests on new helpers | 12 | 0 |

## `test_claude_code.py` — deletion candidates

| Class / Test | Line | Reason | Action |
|---|---|---|---|
| `TestMigrateRepoToCanonical::test_renames_raw_dir_to_canonical` | 179 | Calls `_migrate_repo_to_canonical` directly; method renamed to `_migrate_repo_names`. Behavior preserved via `scan()` behavioral tests. | Delete; add equivalent via `scan()` if coverage gap emerges |
| `TestMigrateRepoToCanonical::test_skips_when_canonical_already_exists` | 197 | Same | Delete |
| `TestPromotePending::test_promotes_pending_to_canonical` | 136 | `_promote_pending` deleted entirely; `.pending/` subtree removed from design | **Delete — no replacement needed** (behavior intentionally gone) |
| `TestPromotePending::test_skips_pending_without_mapping` | 165 | Same | Delete |
| `TestScanProjects::test_stages_unmapped_project_in_pending` | 98 | Asserts `.pending/<raw>/` dir created — behavior removed by new model | Delete; replace with new test asserting local-side `~/.claude/projects/<raw>/.needs_mapping` marker |
| `TestScanProjects::test_pending_marker_contains_raw_name` | 118 | Same — checks marker inside `.pending/` | Delete; replace with new marker-location test |
| `TestSyncFileCopy::test_local_to_repo_copy` | 301 | Calls `_sync_file_copy` directly; method split into `ensure_real_file` (Phase 2) + `_sync_file` (Phase 3) | Delete; rewrite post-refactor as unit test on new helpers |
| `TestSyncFileCopy::test_repo_to_local_copy` | 316 | Same | Delete; rewrite post-refactor |
| `TestSyncFileCopy::test_symlink_migrated_to_real_file` | 332 | Same | Delete; rewrite post-refactor as `ensure_real_file` unit test |
| `TestSyncFileCopy::test_symlink_with_missing_target_restored_from_repo` | 352 | Same | Delete; rewrite post-refactor as `ensure_real_file` unit test |
| `TestSyncFileCopy::test_conflict_delegates_to_merge` | 370 | Same | Delete; rewrite post-refactor as `_sync_file` unit test |
| `TestSyncFileCopy::test_broken_symlink_no_repo_fallback_skipped` | 392 | Same | Delete; rewrite post-refactor as `ensure_real_file` unit test |
| `TestCopyDirFiles::test_copies_all_files_from_source_to_dest` | 412 | `_copy_dir_files` subsumed by `ensure_real_dir` | Delete; rewrite post-refactor as `ensure_real_dir` unit test |
| `TestCopyDirFiles::test_skips_subdirectories` | 430 | Same | Delete; rewrite post-refactor |
| `TestCopyDirFiles::test_conflict_delegates_to_merge` | 450 | Same. Note: `ensure_real_dir` does NOT merge conflicts (Phase 3's job) — this test's assertion changes meaning. | Delete; replace with Phase 3 conflict test |
| `TestCopyDirFiles::test_creates_dest_dir_if_missing` | 473 | Same | Delete; rewrite post-refactor as `ensure_real_dir` unit test |

**Total deletions in `test_claude_code.py`: 16 tests.** (Corrects earlier "14" estimate.)

## `test_claude_code.py` — tests to keep but review

| Class / Test | Line | Why review |
|---|---|---|
| `TestSafetyVerification::test_rmtree_skipped_on_failed_migration` | 258 | `_verify_migration` still exists but its call site moves to Phase 3. Test may need to drive via `scan()` rather than calling internals. |
| `TestSymlinkToCopyMigration::test_symlinked_memory_dir_unmapped_left_alone_in_copy_mode` | 623 | New rule: unmapped = zero repo action. Assertion may need tightening (no `.pending/` either). |

## `test_generic.py` — deletion candidates

**None.** All `TestAdoptDirectory*` / `TestAdoptDirectoryCopy*` / `TestThreeWaySync` tests drive via `target.setup()` or `target.scan()` — they are behavioral and survive the refactor unchanged. Grep confirmed: no test calls `_adopt_directory` or `_adopt_directory_copy` directly.

## Post-refactor additions (not deletions — for completeness)

These are **new** tests to add once the refactor is green, replacing the coverage lost from the deletions above:

| New test | Target | Purpose |
|---|---|---|
| `test_migration_helpers.py::test_ensure_real_file_*` | `src/targets/_migration.py` | Unit tests for `ensure_real_file` (all branches: real file no-op, symlink→real via target, symlink→real via fallback, broken no-fallback) |
| `test_migration_helpers.py::test_ensure_real_dir_*` | `src/targets/_migration.py` | Unit tests for `ensure_real_dir` (including atomic tmp+rename behavior) |
| `test_generic.py::TestModeChange` | `GenericTarget` Phase 1 | Copy → symlink → copy round-trip invalidates stale hashes correctly |
| `test_claude_code.py::TestPhase1Invariants` | `ClaudeCodeTarget` | `_assert_phase1_invariants` raises when raw names remain in repo after Phase 1 |
| `test_claude_code.py::TestUnmappedMarker` | `ClaudeCodeTarget` Phase 2 | Unmapped project creates local `.needs_mapping`, zero repo writes, marker contents correct |
| `test_claude_code.py::TestSyncFile` | `ClaudeCodeTarget._sync_file` | Phase 3 file sync (copies, conflict merge) — behavioral replacement for deleted `TestSyncFileCopy` |

## Approval checklist

- [ ] Confirm all 16 deletions in `test_claude_code.py`
- [ ] Confirm zero deletions in `test_generic.py`
- [ ] Confirm 6 post-refactor additions
- [ ] (Optional) Approve intermediate branch or single-PR strategy before implementation begins
