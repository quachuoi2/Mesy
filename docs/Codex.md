# Codex instructions and memory

How Codex gets persistent guidance, recalls earlier chats, and keeps context during a long chat. This describes the local Codex CLI, IDE extension, and connected desktop host as documented by OpenAI in September 2026. The Agents SDK sandbox has a separate `MEMORY.md` workflow, described below.

## The three kinds of context

| Mechanism | Scope | How it arrives | Best use |
| --- | --- | --- | --- |
| `AGENTS.md` | Global or project directory | Codex discovers it at the start of a run | Rules and conventions that should apply consistently |
| Local memories | Across eligible chats on one Codex host | Codex generates files in the background and can inject relevant memories into later chats | Useful context learned from prior work |
| Chat context and compaction | One continuing chat | The conversation is retained or summarized as it grows | Continuing a long task without replaying every turn |

These are separate mechanisms. A project file named `MEMORY.md` is not a standard Codex instruction file. Keep required project guidance in `AGENTS.md` or checked-in documentation, because recalled memories are selective. [Customization overview](https://learn.chatgpt.com/docs/customization/overview) · [Memories](https://learn.chatgpt.com/docs/customization/memories)

## `AGENTS.md`: instructions loaded before work

Codex builds an instruction chain when a run starts. For the interactive terminal UI, that normally means when the session starts. The chain is assembled in this order:

1. **Global:** From `$CODEX_HOME`, which defaults to `~/.codex`. Codex uses a nonempty `AGENTS.override.md` if present; otherwise it uses `AGENTS.md`.
2. **Project:** From the project root, normally the Git root, down through the current working directory. In each directory, Codex chooses at most one nonempty file: `AGENTS.override.md`, then `AGENTS.md`, then a configured fallback filename.
3. **Order of application:** Global guidance comes first, followed by project files from root to current directory. More specific guidance appears later and can override earlier guidance when they conflict. An override replaces the regular file **in its own directory**; it does not remove guidance from parent directories.

If Codex cannot determine a project root, it checks only the current directory for project guidance. It does not search below the current working directory during this initial walk. Empty files are skipped. The combined instructions have a `project_doc_max_bytes` limit of 32 KiB by default. [AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

Example, when Codex starts in `repo/services/payments/`:

```text
~/.codex/AGENTS.md                    global preferences
repo/AGENTS.md                        repository conventions
repo/services/payments/AGENTS.md      payment-specific guidance
```

If `repo/services/payments/AGENTS.override.md` exists, Codex uses it in place of that directory's `AGENTS.md`. The global and repository files still apply. Put broad rules at the root and narrow rules near the work they govern. Keep instructions short and link to detailed project documents when needed. [AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) · [Customization overview](https://learn.chatgpt.com/docs/customization/overview)

### Alternative filenames and configuration

Other filenames are not discovered automatically. Add them to `project_doc_fallback_filenames` in `$CODEX_HOME/config.toml` if needed:

```toml
project_doc_fallback_filenames = ["TEAM_GUIDE.md"]
project_doc_max_bytes = 65536
```

Codex then checks `AGENTS.override.md`, `AGENTS.md`, and the listed fallbacks, in that order, per directory. A `MEMORY.md` added as a fallback would be treated as **instructions**, not as Codex's generated memory store. Prefer a descriptive instructions filename so the two concepts remain clear. Start a new run or session after changing configuration or instruction files; Codex rebuilds the chain at startup. [AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

### Check what Codex loaded

Start Codex from the intended directory and ask it to summarize its active instructions or list the instruction files it loaded. If results look stale, check `$CODEX_HOME`, look for a nearer `AGENTS.override.md`, and start a fresh session. The official guide also describes optional session or TUI logs for auditing. [AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## Local Codex memories: context from earlier chats

Local memories are **off by default**. Once enabled, Codex can extract useful context from eligible prior chats and save it locally. The work runs in the background after a chat has been idle long enough; it need not happen as soon as a chat ends. Active or short-lived chats can be skipped. Generation may also be skipped when the remaining rate limit is below a configured threshold. OpenAI says generated memory fields are redacted for secrets, but advises reviewing files before sharing them. [Memories](https://learn.chatgpt.com/docs/customization/memories)

The files live under `$CODEX_HOME/memories/`, normally `~/.codex/memories/`. They include summaries, durable entries, recent inputs, and supporting evidence. Codex may inject existing memories into later sessions when memory use is enabled. Treat these files as generated state: inspect them when needed, but use the feature controls and ordinary project docs as your primary way to manage behavior. There is no promise that every past fact will be recalled in every future chat. [Memories](https://learn.chatgpt.com/docs/customization/memories)

### Turn memories on and control each chat

In the desktop app, use **Settings > Personalization > Enable memories**. For config-based setup, add this to `$CODEX_HOME/config.toml`:

```toml
[features]
memories = true
```

The interactive Codex CLI and desktop app offer `/memories` to control whether the **current chat** can use existing local memories and whether it can contribute to future memory generation. These chat choices do not alter the global settings. The IDE extension uses the connected Codex host's local store and its chat controls when memory is enabled there. [Memories](https://learn.chatgpt.com/docs/customization/memories)

The main configuration switches are:

| Setting | Effect |
| --- | --- |
| `memories.generate_memories` | Whether new chats can become inputs for memory generation |
| `memories.use_memories` | Whether existing memories are injected into future sessions |
| `memories.disable_on_external_context` | Exclude chats that used external context, such as MCP calls or web search, from generation |
| `memories.min_rate_limit_remaining_percent` | Minimum remaining rate limit for a generation pass |
| `memories.extract_model`, `memories.consolidation_model` | Override models used for extraction and consolidation |

See the [Memories guide](https://learn.chatgpt.com/docs/customization/memories) and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) for current defaults and accepted values. The older `memories.no_memories_if_mcp_or_web_search` key is accepted as an alias for `memories.disable_on_external_context`.

### Product boundary

ChatGPT web memory and the local Codex memory store are separate. ChatGPT Work uses account and workspace memory settings; it does not use the local Codex store. A Codex IDE session uses the store of the host to which it is connected. [Memories](https://learn.chatgpt.com/docs/customization/memories)

## What `MEMORY.md` means

The [Agents SDK sandbox memory guide](https://developers.openai.com/api/docs/guides/agents/sandboxes) documents a **different** workflow for applications built with `SandboxAgent` and its `memory()` capability. In that workflow, the sandbox workspace contains `memories/memory_summary.md`, `memories/MEMORY.md`, and more detailed rollout records. The SDK injects the short summary at the start of a run; the agent can search `MEMORY.md` and open deeper records when useful. When a sandbox session closes, the SDK can consolidate extracted memories into those files. Preserving them across runs requires preserving or mounting the sandbox memory directory.

That SDK layout does not mean that putting `MEMORY.md` in an ordinary Codex repository will make the CLI or IDE extension automatically load it as memory. To make a human-maintained document reliably available, link to it from `AGENTS.md` and tell Codex when to read it, or use an instruction-file fallback if it really is an instructions document. [AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) · [Sandbox Agents](https://developers.openai.com/api/docs/guides/agents/sandboxes)

## Long conversations and compaction

Current chat history is another form of context. The `/compact` command summarizes a long chat so the task can continue with fewer context tokens. It preserves a working summary **within that conversation**; it does not create a global instruction file or guarantee a new local memory entry. For details, see [Developer commands](https://learn.chatgpt.com/docs/developer-commands). If a decision must survive independently of chat history, record it in the appropriate project documentation; if it is a recurring rule, put it in `AGENTS.md`.

## Where to put information

| Information | Recommended place |
| --- | --- |
| Always-followed repository rules, commands, and conventions | Repository `AGENTS.md` |
| Personal instructions that apply across repositories | `$CODEX_HOME/AGENTS.md` |
| Rules for one subtree | Nested `AGENTS.md` or `AGENTS.override.md` |
| Detailed architecture, decisions, or status that people should review | Checked-in project docs, linked from `AGENTS.md` when relevant |
| Useful context inferred from earlier eligible chats | Codex local memories, if enabled |
| Temporary progress inside a long chat | Chat context and compaction |

## How this relates to Mesy

Mesy's Codex target syncs the **global** `$CODEX_HOME/AGENTS.md` and the native `$CODEX_HOME/memories/` folder into the data repo's `codex/` folder. It leaves project-owned `AGENTS.md` files in their repositories. Codex writes a separate memories folder on each computer, so syncing it across computers can cause conflicts. Configuration is in the [README](../README.md).

## Official references

- [Codex customization overview](https://learn.chatgpt.com/docs/customization/overview)
- [Custom instructions with `AGENTS.md`](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex and ChatGPT memories](https://learn.chatgpt.com/docs/customization/memories)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Developer commands, including `/compact` and `/memories`](https://learn.chatgpt.com/docs/developer-commands)
- [Agents SDK sandbox memory](https://developers.openai.com/api/docs/guides/agents/sandboxes)
