# Rule breaks (not fixed)

Checked on main at 3ce7281 plus the blank-line pass, 2026-09-30. Rules come from `AlfredMemory/claude/global/rules/code.md` and Mesy's `CLAUDE.md`. Real bugs go in `Bugs.md`, not here.

## Seam Law

| Where | Problem | Fix idea |
|---|---|---|
| `base.py` `_sync_file`, `_sync_dir`; `generic.py` `_sync`, `_walk_src_side`, `_walk_repo_side`; `claude_code.py` `_mapped_pair` | Sync mode (symlink or copy) has two implementations but no interface. Each target checks `if self.sync_mode == "copy"` in several places. | One sync-mode interface with a symlink and a copy implementation, chosen once in the factory. |
