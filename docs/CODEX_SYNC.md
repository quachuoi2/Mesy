# Codex synchronization

Mesy supports a dedicated `codex` target. It links selected portable files into a
data repository while keeping the Codex installation, configuration, authentication,
session history, and databases local. The target supports symlink mode only.

## Configuration

Add this section to the **data repository's** `sync.conf`, not the Mesy code repo:

```ini
[target:codex]
type = codex
repo_subdir = codex
sync_mode = symlink

# Optional: otherwise use CODEX_HOME, falling back to ~/.codex.
# source = ~/.codex

# Optional: comma-separated, explicitly selected personal skill folders.
# skills = my-skill, another-skill
# skills_source = ~/.agents/skills

# Optional: export generated Markdown memories after two stable observations.
export_memories = false
export_settle_seconds = 2
```

Keep machine-specific source paths local when using a nonstandard installation.
The daemon's environment, including `CODEX_HOME`, may differ from a terminal's.
Restart Mesy after changing targets or timing settings.

For Codex targets, explicit `type` takes precedence over legacy type inference.
Existing configurations without `type` retain their existing interpretation:
`mapping_conf` means Claude Code; otherwise the target is generic.

Mesy rejects overlapping source ownership, overlapping destination directories,
and destinations escaping the data repository. Do not configure the same source
through both a generic target and a Codex target.

## Managed files

| Local location | Data repository location | Link type |
| --- | --- | --- |
| `$CODEX_HOME/AGENTS.md` | `codex/global/AGENTS.md` | File |
| `$CODEX_HOME/mesy-memory/` | `codex/shared/` | Directory |
| `$CODEX_HOME/mesy-generated/` | `codex/generated/` | Directory |
| `<skills_source>/<selected-name>/` | `codex/skills/<selected-name>/` | Directory |

`mesy-memory` and `mesy-generated` are Mesy-managed locations, not Codex native
memory directories. Setup creates a shared index and appends a marked instruction
block to `AGENTS.md`. The block tells Codex to consult shared notes, use canonical
project names, and treat generated exports as historical evidence rather than
instructions. Existing instruction text is preserved. The block is added once;
subsequent scans do not rewrite it.

Project notes belong under `codex/shared/projects/<canonical-name>/`. Add links to
them in `codex/shared/INDEX.md`. This is an explicit shared notebook, not an
automatic importer into Codex's internal memory store. Codex's sandbox still
controls whether it may write the resolved data-repository path. Grant only the
required data directory through your normal Codex permission settings.

Project-owned `AGENTS.md` files remain in each project's Git repository. Mesy does
not crawl workspaces or replace tracked project instructions. A local
`AGENTS.override.md` stays local; Mesy logs when it shadows the shared global file.

Selected skills include their nested scripts and assets, retaining executable
permissions where supported. Bundled/system skills and plugin caches are not
selected automatically. Nested symlinks and special files inside managed content
are rejected rather than followed into other directories.

## Preview and activation

Activate Mesy's virtual environment first. These commands operate only on Codex
targets and do not install/restart services, commit, or push:

```bash
source .venv/bin/activate
python setup.py --codex preview --data-dir /path/to/AlfredMemory
python setup.py --codex adopt --data-dir /path/to/AlfredMemory
```

Without `--data-dir`, the commands use repositories listed in `repos.conf`.
Preview prints planned links and recovery locations without creating files.

Stop the existing daemon before adoption or detachment to avoid simultaneous
migration. Adoption can also happen during the daemon's initial scan, but explicit
preview/adoption provides a clearer first-run workflow. Resolve any reported
conflicts, restart Mesy, and then start a fresh Codex session to verify that shared
instructions and the memory index are loaded. An already-running conversation
does not necessarily reload instructions after a pull.

On Windows, enable Developer Mode or run with the privileges required for
symlinks. Mesy probes symlink creation before moving each source. Failure leaves
that source intact; it does not silently substitute an unsynchronized copy.

## Adoption, editing, and recovery

Existing regular files and directories are copied and verified before their local
locations become symlinks. Missing files are adopted in either direction.
Disjoint directories are combined, including nested and empty directories.

If both locations contain different versions of an existing file, Mesy preserves
the originals and saves conflict copies locally. Resolve the difference so the
locations agree, then retry. It does not concatenate competing instructions or
scripts into the active file.

An editor may replace a file symlink through an atomic save. Mesy repairs this
automatically only if the repository still matches the last observed linked
version, or the two files already match. Otherwise, it reports a conflict. File
baselines are local and isolated per target/source. Directory links survive
atomic replacement of files inside the linked directory.

Recovery data lives at:

```text
<data_repo>/.mesy-local/codex/<target-id>/
  backups/       # Verified originals and displaced files/directories
  conflicts/     # Divergent local and repository versions
  links/         # File-link repair baselines
  device-id      # Persistent device identifier for exports
```

These paths are outside Mesy's automatic staging allowlist. Add `.mesy-local/`
to the data repository's local `.git/info/exclude` before using manual blanket
`git add` commands. Never synchronize these recovery files across devices.

To detach links while retaining the latest synchronized content:

```bash
python setup.py --codex detach --data-dir /path/to/AlfredMemory
```

Stop Mesy first. After detachment, remove/disable the Codex target before restarting
the daemon, or it will adopt the files again. Detachment restores regular local
files; it does not restore historical contents or delete the data repository.
Pre-adoption copies remain in `backups/` for manual historical recovery. If a
process stops during adoption, also inspect `.mesy-original-*` beside the source.

Deleting a managed link alone is treated as accidental unlinking and repaired.
Deleting a file inside a linked directory deletes the underlying repository file
and propagates through Git. The shared index and global bootstrap instructions are
managed entry points and may be recreated; disable the target to retire them.

## Git behavior

A data repository containing a Codex target uses an isolated-merge transport for
**all its targets**, including existing Claude/OpenClaw targets. Repositories
without a Codex target retain the legacy transport.

The transport commits only configured paths using `git commit --only`. Unrelated
staged and modified tracked files remain untouched and defer remote integration.
It fetches `origin` and uses the current local branch name as the remote branch.
Use a normal checked-out branch with Git identity and an `origin` remote configured.
The legacy transport derives the branch the same way, falling back to `main` on a
detached HEAD.

Remote merges run in a temporary detached worktree. Only a successful merge is
fast-forwarded into the live data repository. Conflicts are saved under
`.mesy-local/git-conflicts/<local-head>-<remote-head>/`, with base/local/remote
versions where available. No conflict markers are published into linked live files.
Resolve the underlying Git divergence manually before retrying; editing the saved
copies alone does not resolve the branch history.

Git rechecks local changes during publication. There is no transaction spanning
every file read by a running Codex process; readers can observe individual updates
during a clean fast-forward. Quiesce writers when resolving conflicts or migrating.

Push failures leave local commits intact. Periodic synchronization retries them
even if no additional files changed. Filesystem event handling includes renames,
deletions, and selected non-Markdown assets. Read-only events and local recovery
files do not trigger synchronization. Polling remains available without watchdog.

A failed scan blocks commits for the whole data repository, not only the failing
target. In a repository containing a Codex target, pulls stop as well, because
both directions run behind the same scan gate. The failure is logged once at ERROR
until the message changes, and recovery is logged at INFO. Resolve the reported
conflict and wait for the next scan; no restart is required.

## Optional generated-memory exports

Set `export_memories = true` to publish UTF-8 Markdown from the native
`$CODEX_HOME/memories/` directory. This does **not** enable Codex memory generation.
Enable native memories separately in Codex if desired.

Mesy requires two matching observations separated by `export_settle_seconds`, then
rechecks the content before publishing. Observations occur on scans, so latency
also depends on scan settings. A snapshot is limited to 16 MiB. Symlinks, hidden
paths, non-Markdown files, and `extensions/` (including computer-history data) are
excluded. Exports do not read SQLite databases or raw conversation histories.

Snapshots are immutable and stored under:

```text
codex/generated/<device-id>/
  INDEX.md
  snapshots/<content-hash>/
    manifest.json
    <exported Markdown files>
```

Each device publishes its own index, content hashes, and capture timestamp.
Repeated unchanged scans create no new snapshot or index rewrite. Older snapshots
are retained; there is no automatic retention pruning. Deleting native memories
does not erase historical exports. Review/export only content you intend to share.

The default device ID is generated and stored locally. Do not copy
`.mesy-local/` between devices. An explicit `device_id` is supported for deployments
with machine-local configuration; do not give multiple writers the same value in
a shared `sync.conf`.

Other devices can consult exports through their `mesy-generated` link. Mesy never
copies them into another installation's native `memories/` directory and does not
claim compatibility with native memory-store replication.

## Validation

```bash
source .venv/bin/activate
python -m pip install pytest watchdog
python -m pytest tests/ -q
```

Tests use temporary filesystems and mocked Git commands, never live memory homes
or remote pushes. Coverage includes adoption, rollback, atomic saves, conflicting
edits, nested assets, exports, source ownership, event filtering, isolated merges,
and offline retries. Windows link behavior is gated through a capability probe;
native Windows and a live two-machine Codex session still require deployment testing.

Official references:

- [Codex instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex local memories](https://learn.chatgpt.com/docs/customization/memories)
- [Personal skills and symlink support](https://learn.chatgpt.com/docs/build-skills)
