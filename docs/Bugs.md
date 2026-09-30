# Known bugs (not fixed)

Checked against the code on branch `refactor` at `aea9c94`, 2026-09-26.

| # | Bug | Where | Hits today? |
|---|---|---|---|
| 1 | Copy mode makes a conflict out of every one-sided edit (Claude Code, Codex) | `src/targets/base.py` `_sync_file_copy`, `_sync_dir_copy` | Only in copy mode; fix is in `stash@{0}` |
| 2 | Conflict merge breaks nested frontmatter and drops repo values | `src/conflict.py` `split_frontmatter`, `merge_conflicting_file` | Yes, on any memory-file conflict |
| 3 | Rebase conflict labels are swapped | `src/git_sync.py:124-128` | Yes, on any rebase conflict |
| 4 | Rebase conflict in a file without frontmatter gets no `conflict: true` | `src/git_sync.py` `_flag_conflict` | Yes, e.g. `CLAUDE.md`, `AGENTS.md` |
| 5 | Two copy-mode generic targets in one data repo erase each other's hashes | `src/targets/generic.py:42-46`, `src/sync_state.py` | No (one copy target per repo today) |
| 6 | A sync-mode change clears every hash in the repo's hash file | `src/targets/generic.py` `_migrate` | No (same condition as 5) |
| 7 | Claude Code copy mode syncs two local files to one repo file | `src/targets/claude_code.py` | No (neither file pair exists today) |
| 8 | Failed pulls are logged at debug only | `src/git_sync.py` `pull_remote` | Yes, silently |
| 9 | `setup.py` crashes on any symlink-mode generic target | `setup.py:517,520` | Yes, on every `python setup.py` with such a target |

## 1. Copy mode: every one-sided edit becomes a conflict

- **What happens:** Claude Code and Codex in copy mode merge both versions with conflict markers whenever local and repo differ, even when only one side changed. Only generic targets use stored hashes to tell which side changed.
- **Status:** fixed in `stash@{0}` (Point 4 in `FixNotes.md`). Finish and commit it.

## 2. Conflict merge breaks frontmatter

`split_frontmatter` reads only `key: value` lines, one per line.

- **Nested keys are flattened.** `metadata:` has no `": "`, so it is dropped, and the indented `  type: feedback` becomes a top-level `type`. Every memory file written by current Claude Code uses nested `metadata:`.
- **Multi-line values are cut** to the lines that contain `": "`.
- **Repo values are lost:** `{**repo_fm, **local_fm}` keeps only the local value for keys both sides have, so a repo-side `description` change is lost. This breaks the "nothing is lost in a conflict" rule.
- **Fix idea:** keep both frontmatter blocks as raw text inside the conflict sections, and add only `conflict: true` at the top.

## 3. Rebase conflict labels are swapped

- **What happens:** during `git pull --rebase`, the `<<<<<<<` side is the remote commit (the rebase base) and the `>>>>>>>` side is the local commit being replayed. `_replace_markers` labels the first side "local (hostname)" and the second "remote".
- **Effect:** a person resolving the conflict keeps the wrong version.
- **Fix:** swap the two labels. Add a test with a real rebase-conflict text.

## 4. Rebase conflicts in files without frontmatter are not flagged

- **What happens:** `_flag_conflict` adds `conflict: true` only when the file already starts with `---`. Otherwise the text is returned unchanged.
- **Effect:** the rule "if a memory file has `conflict: true`, flag it" misses these files, even though the README says every conflict gets the flag. `merge_conflicting_file` (copy mode) always adds frontmatter, so only rebase conflicts are affected.
- **Also:** a file that already has `conflict: true` gets a second `conflict:` line.
- **Fix:** add a frontmatter block when there is none, and skip the line when the key already exists.

## 5. Two copy-mode generic targets in one repo erase each other's hashes

- **What happens:** each copy-mode `GenericTarget` creates its own `SyncState` for the same `.mesy_sync_hashes.json`. Each one loads the file once at start-up and writes its whole dictionary back on save. The last one to save wins.
- **Effect:** the other target's files lose their stored version. At the next sync the local side wins, which can undo an edit pulled from another computer.
- **Status:** fixed in `stash@{0}` (Point 4: one `SyncState` per data repo, in `factory.py`).

## 6. A sync-mode change clears other targets' hashes

- **What happens:** `GenericTarget._migrate` calls `clear_target_hashes`, which removes every key in the shared hash file, not just this target's keys.
- **Effect:** same as bug 5, for every other copy-mode target in the repo.
- **Fix:** clear only keys under this target's `repo_subdir`. Not fixed in the stash.

## 7. Claude Code copy mode: two local files, one repo file

- **What happens:** `~/.claude/projects/<x>/CLAUDE.md` and `~/.claude/projects/<x>/memory/CLAUDE.md` both map to `<canonical>/CLAUDE.md` in the repo. If both exist, they overwrite each other on every scan.
- **Fix:** remove the project `CLAUDE.md` sync. Claude Code never reads that file (TODO in the Mesy memory).

## 8. Failed pulls are hidden

- **What happens:** `pull_remote` logs any non-zero `git pull --rebase` at debug level with the message "offline?".
- **Effect:** a pull refused because of uncommitted changes, or any other git error, looks the same as being offline and never appears in `log.log` at the default level. This is how the rename-save bug (Point 3) stayed hidden.
- **Fix:** log at warning, once per distinct error, the same way scan failures are logged.

## 9. `setup.py` crashes on generic targets

- **What happens:** `setup_generic_target` takes the source folder as `src`, but its two log lines use `{source}`, a name that does not exist. Python stops with `NameError: name 'source' is not defined` (checked on main at 3ce7281, 2026-09-30).
- **Effect:** setup stops partway through the repo list; later repos and the daemon install never run.
- **Why tests missed it:** no test calls `setup_generic_target`.
- **Fix:** use `{src}` in both lines, and add one test that runs it. The `src`/`source` mix is listed in `RuleBreaks.md`.
