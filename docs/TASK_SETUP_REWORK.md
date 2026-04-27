# Task: Rework `setup.py` for multi-repo / `repos.conf` world

## Context

The daemon recently migrated from a single-`DATA_DIR` model (resolved via
`.env` / `MESY_DATA_DIR` / sibling fallback) to a **required** `repos.conf` at
the repo root listing one or more data repos.

The runtime (`src/config.py`, `src/main.py`) no longer reads `.env` or exports
`DATA_DIR`. `src/targets/claude_code.py` requires an explicit `data_dir`
argument. Tests are green.

`setup.py` was **not** updated in that pass — it still operates on a single
data dir resolved through the old `.env` / sibling logic, writes `.env` to
persist the choice, and iterates one `sync.conf`. Running it now sets up only
one repo and leaves `repos.conf` empty, so the daemon refuses to start.

## Goal

`python setup.py` should onboard a new machine for the **multi-repo** world:

1. Let the user add/confirm N data repos and write them into
   `REPO_DIR/repos.conf`.
2. For each data repo, read its own `sync.conf` and perform the initial
   filesystem migration (Claude Code symlinks + generic targets) scoped to
   **that** repo's path.
3. Install the systemd user unit (unchanged) or Windows Scheduled Task
   (unchanged). The setup MUST work on BOTH Linux and Windows.

No `.env`. No `MESY_DATA_DIR`. No module-level `DATA_DIR` global.

## Concrete changes

### Delete
- [setup.py:28-44](setup.py#L28-L44) — `_load_dotenv()` and the top-level call to it
- [setup.py:47-64](setup.py#L47-L64) — `_resolve_data_dir()`
- [setup.py:67-68](setup.py#L67-L68) — module-level `DATA_DIR` / `PROJECTS_CONF_PATH` globals
- [setup.py:71-90](setup.py#L71-L90) — `_write_data_dir_to_env()`
- [setup.py:569-571](setup.py#L569-L571) — the `global DATA_DIR, PROJECTS_CONF_PATH` reassignment in `main()`
- `.env` file at repo root (currently dead — only a commented line)

### Replace
- **`prompt_data_dir()` → `prompt_repos_conf()`**

  New behavior: read `REPO_DIR/repos.conf` (via `src.config.load_repos`), show
  existing entries, offer to add more. Write updates back to `repos.conf`.
  Returns a `list[dict]` with `name` and `path` keys (same shape as
  `load_repos()`).

  Suggested prompt flow:
  - If `repos.conf` already has entries → print them, ask "Add another data
    repo? [y/N]"
  - If empty/missing → require at least one
  - Per repo: prompt for a short name (e.g. `alfred-memory`) and a path.
    Reject paths inside `REPO_DIR`. Offer to `mkdir -p` if the path does not
    exist.
  - Append `[repo:<name>]\npath = <absolute path>\n` to `repos.conf` (use
    `ConfigParser` — do not string-concatenate)

- **Per-repo setup functions** — `setup_claude_code_symlinks`,
  `migrate_repo_to_canonical`, `load_project_mappings`,
  `setup_generic_target`, `load_sync_conf_targets` — all currently read the
  module-level `DATA_DIR`. Convert each to take `data_dir: Path` as an
  explicit argument and stop touching globals.

  Note: `setup.py` has its own local copy of `load_project_mappings`
  ([setup.py:257-271](setup.py#L257-L271)) — consider importing from
  `src.config` instead of duplicating. The signature there is already
  `load_project_mappings(data_dir)`.

- **`main()`** — after `prompt_repos_conf()`, loop over the returned repo
  list and call the per-repo setup functions for each one. Daemon install is
  called once at the end, outside the loop.

### Keep as-is
- `merge_conflicting_file`, `_split_frontmatter`, `make_file_symlink`,
  `make_symlink` — pure helpers, no globals
- `install_systemd_service`, `install_task_scheduler`, `install_daemon` —
  daemon install is repo-count-agnostic (unit file already dropped
  `EnvironmentFile=`)

## Design decisions (resolved)

- **Scope**: setup.py is an initialize/add-repo script only. No remove/edit
  flow — users hand-edit `repos.conf` to delete or rename.
- **Re-run idempotency**: existing entries in `repos.conf` are shown and
  skipped; setup only prompts to *add* more. Per-repo migration functions
  are expected to be idempotent (symlinks already present = noop).
- **Single-AI-per-user model**: each user is expected to use one AI type
  (Claude Code, OpenClaw, etc.) across all their machines. Multiple data
  repos declaring the same target type (e.g. two `[target:claude_code]`)
  is not supported but not fatal:
  - Scan all repos' `sync.conf` after prompt, before running migrations.
  - On duplicate target type across repos: **warn** and **skip** the
    duplicate. First-seen repo (by `repos.conf` order) wins.
  - Do not error — user may intentionally have a second repo for other
    targets.
- **Missing `sync.conf`**: if a repo's `sync.conf` is absent, error out
  and offer to write a minimal template (`[mesy]` + empty targets).
- **Name collisions** (`[repo:<name>]` duplicates, or multiple repos
  claiming the same project raw_name): assume unique for now. No
  validation. Handle in a future task.
- **Repo name validation**: none. Accept whatever the user types.
- **Path-inside-REPO_DIR check**: use `Path(user_input).resolve()` before
  comparing to `REPO_DIR.resolve()`. If the resolved path is inside
  `REPO_DIR`, reject and print the resolved path in the error so the
  user can see why (handles symlink-escapes).
- **No `.env` anywhere**: `.env` file is deleted; no code path reads or
  writes it; no `MESY_DATA_DIR` env var is consulted.
- **Windows path handling**: keep `Path` throughout, same as current code.

## Tests to add / update

- `setup.py` is currently untested. Minimum new coverage:
  - `prompt_repos_conf` appends a new `[repo:*]` section without clobbering
    existing ones (use `tmp_path` for `REPO_DIR`, feed input via
    `monkeypatch.setattr("builtins.input", ...)`)
  - Refuses a path inside `REPO_DIR`
  - Per-repo `setup_claude_code_symlinks(data_dir=...)` is called once per
    repo in `main()` (mock the symlink helpers)
- Remove/update any test that relied on `setup.DATA_DIR` or
  `setup._resolve_data_dir`.

## Docs to refresh after the rewrite

- [README.md](README.md) (if it documents onboarding) — the "set
  `MESY_DATA_DIR` or accept the sibling default" story is gone
- [CLAUDE.md](CLAUDE.md) already updated for the runtime; add a one-liner
  under "Quick Reference" pointing at `python setup.py` as the way to
  populate `repos.conf`

## Validation checklist

- [ ] Fresh clone on a machine with no `repos.conf` → `python setup.py`
      walks user through adding ≥1 repo and writes a valid `repos.conf`
- [ ] Re-running setup on the same machine does not duplicate entries
- [ ] `python sync.py` starts the daemon and logs "Loaded N repo(s) from
      repos.conf"
- [ ] `python -m pytest tests/` stays green
- [ ] `.env` file is gone; `grep -rn "MESY_DATA_DIR\|_load_dotenv" .` and
      `grep -rn "\.env\b" src/ setup.py mesy.service` both return nothing
- [ ] Two repos declaring `[target:claude_code]` → setup warns and skips
      the second; first-repo symlinks still created
- [ ] Repo path resolving inside `REPO_DIR` (direct or via symlink) is
      rejected with the resolved path printed
- [ ] Missing `sync.conf` in a repo → setup offers to create a template
