# Fix notes: review point 4

Status as of 2026-09-30. Points 2 (`setup.py` deletes subfolders, aea9c94) and 3 (watchdog misses rename saves, 3430f5f) are fixed and committed.

Point 4 is the only one left. The work is in `stash@{0}` (made on branch `refactor` at aea9c94), tests pass (178), not committed. Bring it back with `git stash pop`.

## Point 4: copy mode turns every edit into a conflict

**Problem:** in copy mode, Claude Code and Codex merged both versions with conflict markers whenever local and repo differed, even when only one side had changed. Only generic targets had three-way hashes (a stored hash of the last synced version, used to tell which side changed).

**Fix (in stash):**
- `src/targets/base.py`: copy mode for all targets now uses the three-way logic, moved in from `generic.py`: `_three_way_sync`, `_take`, `_repo_key`. `_sync_dir_copy` syncs each file (subfolders included) through the same logic. The hash store (`SyncState`) is created in the base class for copy-mode targets.
- Identical files now get their version stored the first time they are seen (`_remember`). Without this, the first edit from another computer after switching to copy mode had no stored version, so the stale local copy "won" and undid the edit.
- A target whose source is the data repo itself (DMNotes: `repo_subdir = .`) is skipped by a same-file check, so its hash file (tracked in git there) does not change.
- Stored text is read with `errors="replace"`, so binary files do not stop the scan; only merging needs real text.
- `src/factory.py`: one `SyncState` per data repo, shared by its targets. Separate objects writing the same hash file would erase each other's entries.
- `src/targets/generic.py`: its copy-mode code and its `__init__` are removed; it uses the base class.
- `src/targets/claude_code.py`: accepts and passes on `sync_state`.
- `tests/test_targets/test_generic.py`: 3 copy-mode tests now patch `src.targets.base.merge_conflicting_file`.

**Rule kept from generic:** no stored version and the files differ → local (source) wins.

**Done (in stash):**
1. `tests/test_targets/test_claude_code.py`: `TestSyncFile.test_copy_mode_conflict_delegates_to_merge` and `TestSyncDir.test_copy_mode_dir_sync_conflict_delegates_to_merge` expect a merge with no stored version. Store a base version first (`isolated_sync_state.set_hash(...)`) so both sides really changed.
2. Add a test: identical local and repo, sync, then change the repo only, sync again: local gets the repo version, no merge.
3. README, under the copy-mode table: one sentence on the no-stored-version rule and identical files getting a stored version.

**Known limits (not fixed):**
- Claude Code copy mode syncs `~/.claude/projects/<x>/CLAUDE.md` and `memory/CLAUDE.md` to the same repo file. If both exist, the two copies can overwrite each other on every scan. No such files exist today. Removing the project `CLAUDE.md` sync (already a TODO) fixes it.
- Generic's `_migrate` clears every hash in the shared file when its sync mode changes, including other targets' hashes in the same repo.

**Commit message:** `Copy mode: three-way sync for Claude Code and Codex too`.
