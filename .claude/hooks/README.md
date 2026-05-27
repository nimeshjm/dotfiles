# Claude Code OTel hooks

Full OpenTelemetry instrumentation for interactive Claude Code sessions,
via shell-command hooks. Covers every meaningful hook event the CLI exposes.

## What this captures

| Dimension | How | Honeycomb derivation |
|---|---|---|
| **Session length** | `session.duration_ms` on each `session.end` span (computed from start-time state file) | `MAX(session.duration_ms) GROUP BY session.id` |
| **Cost per session** | `gen_ai.request.model` + token counts on every `turn.stop` span | `SUM(input_tokens * price_in) + SUM(output_tokens * price_out) GROUP BY session.id, gen_ai.request.model` |
| **Cache effectiveness** | `gen_ai.usage.cache_hit_ratio` pre-computed on each stop span | `AVG(gen_ai.usage.cache_hit_ratio) GROUP BY gen_ai.request.model` |
| **Prompt effectiveness** | `turn.id` links every tool span and the stop span back to the originating prompt | `COUNT(tool spans) GROUP BY turn.id` — tool calls per prompt; filter by `gen_ai.tool.success=false` to find failing turns |

## Install

```bash
# 1. Run the installer (copies hooks, merges settings.json)
cd dotfiles/.claude
python3 install.py                     # Honeycomb (default)
python3 install.py --backend signoz    # SigNoz self-hosted

# 2. Install OTel Python packages (once per machine)
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

## Backend selection

`install.py` supports two OTel backends, selected via `--backend`.

### Honeycomb (default)

```bash
python3 install.py
```

- Sends spans to `https://api.honeycomb.io` via OTLP/HTTP.
- Requires a Honeycomb ingest key (`hcaik_…`) — edit the `OTEL_EXPORTER_OTLP_HEADERS` placeholder in `~/.claude/settings.json` after install.
- Writes rendered panel definitions to `~/.claude/honeycomb_panels.json`.
- Board creation is done via the Honeycomb MCP (see §Creating the Honeycomb board below).

### SigNoz (self-hosted)

```bash
python3 install.py --backend signoz
# Optional: POST dashboard via API instead of manual import
python3 install.py --backend signoz \
    --signoz-url http://localhost:8080 \
    --signoz-api-key YOUR_KEY
```

- Sends spans to `http://localhost:4318` (SigNoz self-hosted OTLP/HTTP receiver).
  Change the endpoint by editing `OTEL_EXPORTER_OTLP_ENDPOINT` in `~/.claude/settings.json`.
- No auth header required for self-hosted.
- Writes `~/.claude/signoz_dashboard.json` with 16 pre-built widgets.
- **Import the dashboard**: SigNoz UI → Dashboards → New Dashboard → Import JSON →
  paste the contents of `~/.claude/signoz_dashboard.json`.
- If `--signoz-api-key` is supplied, also POSTs the dashboard to the SigNoz API
  (`/api/v1/dashboards`) — no manual import needed.

### API keys

Two separate Honeycomb keys are used, with different scopes:

| Key | Purpose | Where used |
|-----|---------|------------|
| **Ingest key** (`hcaik_…`) | Send spans to Honeycomb | `OTEL_EXPORTER_OTLP_HEADERS` env var in shell profile |
| **Configuration key** | Create boards and queries via the Management API | `HONEYCOMB_CONFIG_KEY` env var at install time |

**Getting an ingest key**: Honeycomb UI → *Team Settings* → *API Keys* → *Create Ingest Key*.

**Getting a configuration key**: Honeycomb UI → *Team Settings* → *API Keys* → *Create API Key*.
Under *Permissions*, enable at minimum: **Events**, **Boards**. Queries permission is not required — `install.sh` uses the board-creation path, which handles query creation internally.

### Creating the Honeycomb board

After running `python3 install.py`, the file `~/.claude/honeycomb_panels.json` contains
the 16 pre-built panel definitions in Honeycomb-native query format.

Open Claude Code with the Honeycomb MCP server configured and prompt:

```
"Create a Honeycomb board called 'Claude Code Sessions' using the
 panel definitions in ~/.claude/honeycomb_panels.json"
```

Claude reads the JSON, runs each query via the Honeycomb MCP, and creates the board —
no management key required. Board creation is idempotent — it skips silently if a board
named *Claude Code Sessions* already exists.

## Spans emitted

| Hook event | Span name | Key attributes |
|---|---|---|
| SessionStart | `claude_code.session.start` | session.id, session.trigger |
| SessionEnd | `claude_code.session.end` | session.id, session.end_reason |
| UserPromptSubmit | `claude_code.user_prompt` | turn.id, prompt.char_length, prompt.word_count, command.name* |
| PostToolUse | `claude_code.tool` | turn.id, gen_ai.tool.name, gen_ai.tool.success=true, tool.duration_ms, edit.lines_added/removed* |
| PostToolUseFailure | `claude_code.tool` | turn.id, gen_ai.tool.name, gen_ai.tool.success=false, error.message, error.type |
| Stop | `claude_code.turn.stop` | turn.id, gen_ai.request.model, stop_reason, all 4 token counts, cache_hit_ratio |
| StopFailure | `claude_code.turn.stop_failure` | error.type |
| SubagentStart | `claude_code.subagent.start` | agent.id, agent.type |
| SubagentStop | `claude_code.subagent` | agent.id, agent.type, agent.duration_ms |
| PreCompact | `claude_code.context.pre_compact` | compaction.trigger, context.tokens_before |
| PostCompact | `claude_code.context.compact` | compaction.trigger, tokens_before/after/saved, compaction.duration_ms |
| PermissionRequest | `claude_code.permission.request` | gen_ai.tool.name, tool_use_id, permission.mode |
| PermissionDenied | `claude_code.permission.denied` | gen_ai.tool.name, permission.deny_reason, permission.decision_ms |
| Notification | `claude_code.notification` | notification.type, notification.message |

\* `command.name` — only present when prompt starts with `/`  
\* `edit.lines_added` / `edit.lines_removed` — only present on Edit tool spans  
\* `write.lines` — only present on Write tool spans

Every span also receives `session.id`, `cwd`, `git.repo`, and `git.origin` from the shared emitter.

> **Note on `git.*` attributes**: only SSH-shaped remotes (`git@host:org/repo.git`) populate
> `git.origin` and `git.repo`. Repos using HTTPS remotes will show empty `git.*` attributes
> by design — HTTPS URLs may contain embedded credentials and are never exported to spans.

**Disabled hooks** (files kept, wiring removed from settings.json):
- `CwdChanged` — redundant; `cwd` is already on every tool span
- `PostToolBatch` — reconstructable in Honeycomb by grouping tool spans on `session.id + time`
- `PreToolUse` — still runs to write the start-time state file, but emits no span

## Derived metrics (copy-paste Honeycomb queries)

Paste any of these into the Honeycomb query builder: **New Query → `{ }` JSON icon → paste**. Adjust `time_range` (seconds) as needed: `86400` = 24h, `604800` = 7d.

**Session duration**:
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "MAX", "column": "session.duration_ms"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.session.end"}
  ],
  "filter_combination": "AND",
  "breakdowns": ["session.id"],
  "orders": [{"op": "MAX", "column": "session.duration_ms", "order": "descending"}],
  "limit": 100
}
```

**Cost per session** (substitute model prices):
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "SUM", "column": "gen_ai.usage.input_tokens"},
    {"op": "SUM", "column": "gen_ai.usage.output_tokens"},
    {"op": "SUM", "column": "gen_ai.usage.cache_read_tokens"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.turn.stop"}
  ],
  "filter_combination": "AND",
  "breakdowns": ["session.id", "gen_ai.request.model"],
  "orders": [{"op": "SUM", "column": "gen_ai.usage.input_tokens", "order": "descending"}],
  "limit": 100
}
```

**Cache hit ratio over time**:
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "HEATMAP", "column": "gen_ai.usage.cache_hit_ratio"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.turn.stop"}
  ],
  "filter_combination": "AND",
  "limit": 10
}
```

**Tool calls per prompt** (requires `turn.id`):
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "COUNT"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.tool"}
  ],
  "filter_combination": "AND",
  "breakdowns": ["turn.id", "session.id"],
  "orders": [{"op": "COUNT", "order": "descending"}],
  "limit": 100
}
```

**Permission friction** (avg decision latency by tool):
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "AVG", "column": "permission.decision_ms"},
    {"op": "COUNT"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.permission.denied"}
  ],
  "filter_combination": "AND",
  "breakdowns": ["gen_ai.tool.name"],
  "orders": [{"op": "AVG", "column": "permission.decision_ms", "order": "descending"}],
  "limit": 25
}
```

**Lines of code written per session**:
```json
{
  "time_range": 86400,
  "calculations": [
    {"op": "SUM", "column": "edit.lines_added"},
    {"op": "SUM", "column": "edit.lines_removed"}
  ],
  "filters": [
    {"column": "name", "op": "=", "value": "claude_code.tool"},
    {"column": "gen_ai.tool.name", "op": "=", "value": "Edit"}
  ],
  "filter_combination": "AND",
  "breakdowns": ["session.id"],
  "orders": [{"op": "SUM", "column": "edit.lines_added", "order": "descending"}],
  "limit": 100
}
```

## Honeycomb board

`install.py` creates a pre-built **Claude Code Sessions** board in the `claude` environment with 16 panels arranged in logical groups:

**Row 1 — Activity counts** (bar charts)

| Panel | Query | Display |
|-------|-------|---------|
| Sessions Started | `COUNT` of `claude_code.session.start` | Bar chart |
| Tool Calls | `COUNT` of `claude_code.tool` | Bar chart |
| User Prompts | `COUNT` of `claude_code.user_prompt` | Bar chart |

**Row 2 — Session health** (bar charts)

| Panel | Query | Display |
|-------|-------|---------|
| Session Duration (avg + p95) | `AVG` + `P95` of `session.duration_ms` on `claude_code.session.end` | Bar chart |
| Cache Hit Ratio | `AVG(gen_ai.usage.cache_hit_ratio)` on `claude_code.turn.stop` | Bar chart |
| Model Usage | `COUNT` of `claude_code.turn.stop`, breakdown by `gen_ai.request.model` | Bar chart |

**Row 3 — Token usage** (full-width bar chart)

| Panel | Query | Display |
|-------|-------|---------|
| Token Usage | `SUM(input_tokens)` + `SUM(cache_read_tokens)` + `SUM(output_tokens)` on `claude_code.turn.stop` | Bar chart |

**Rows 4–6 — Tool and session tables**

| Panel | Query | Display |
|-------|-------|---------|
| Tool Failure Rate % | `failed / total * 100` formula, breakdown by `gen_ai.tool.name` | Table |
| Tool Duration (avg + p95) | `AVG` + `P95` of `tool.duration_ms`, breakdown by `gen_ai.tool.name` | Table |
| Lines Edited per Session | `SUM(edit.lines_added)` + `SUM(edit.lines_removed)`, breakdown by `session.id` | Table |
| Prompts per Session | `COUNT` of `claude_code.user_prompt`, breakdown by `session.id` | Table |
| Tokens per Session | `SUM` of input + cache + output tokens, breakdown by `session.id` | Table |

**Row 7 — Diagnostics**

| Panel | Query | Display |
|-------|-------|---------|
| Stop Reason Distribution | `COUNT` of `claude_code.turn.stop`, breakdown by `agent.stop_reason` | Table |
| Subagent Activity | `COUNT` + `AVG(agent.duration_ms)` of `claude_code.subagent`, breakdown by `agent.type` | Table |
| Context Compaction | `COUNT` + `SUM(context.tokens_saved)` of `claude_code.context.compact` | Table |

**Row 8 — Permissions**

| Panel | Query | Display |
|-------|-------|---------|
| Permission Denials | `COUNT` of `claude_code.permission.denied`, breakdown by `gen_ai.tool.name` | Table |

All panels default to the last 24 h. The board time window can be changed interactively in the Honeycomb UI without affecting the saved queries.

To recreate the board after deleting it, run `python3 .claude/install.py` first (to refresh
`~/.claude/honeycomb_panels.json`), then open Claude Code with the Honeycomb MCP server and prompt:

```
"Create a Honeycomb board called 'Claude Code Sessions' using the
 panel definitions in ~/.claude/honeycomb_panels.json"
```

## Opt-in content capture

Set in `.claude/settings.json` env section. All off by default.

| Env var | What it adds | Privacy risk |
|---|---|---|
| `OTEL_LOG_USER_PROMPTS=1` | Prompt text (first 2000 chars) on `user_prompt` spans | Sends prompt content to OTLP backend — may contain secrets or PII |
| `OTEL_LOG_TOOL_DETAILS=1` | Tool input args on `tool` spans | May expose file paths, code, or commands |
| `OTEL_LOG_TOOL_CONTENT=1` | Tool output on `tool` spans | May expose file contents or command output |

> **Warning**: enabling any of these options ships potentially sensitive text to your OTLP
> backend (Honeycomb). Review your data retention and access policies before enabling in
> shared or production environments. Captured text may include API keys, passwords, and
> personal data.

## Testing a single hook manually

```bash
# PostToolUse (Edit tool)
echo '{"session_id":"test123","cwd":"/tmp","tool_name":"Edit","tool_use_id":"tu_1",
  "tool_input":{"old_string":"foo","new_string":"bar"},"duration_ms":42}' \
  | python3 ~/.claude/hooks/hook_post_tool_use.py

# Stop (with model and token usage)
echo '{"session_id":"test123","cwd":"/tmp","stop_reason":"end_turn",
  "model":"claude-sonnet-4-6",
  "usage":{"input_tokens":1000,"output_tokens":200,"cache_read_input_tokens":800,"cache_creation_input_tokens":0}}' \
  | python3 ~/.claude/hooks/hook_stop.py
```

## Applying globally (all projects)

Put the hooks in `~/.claude/hooks/` and configure `~/.claude/settings.json`.
Use absolute paths in the command fields instead of `${CLAUDE_PROJECT_DIR}`.

## Known limitations

- **SDK init overhead**: Each hook is a short-lived Python process that instantiates a new
  `TracerProvider` and `SimpleSpanProcessor` on every call (~100–300ms). For high-frequency
  tool use sessions, PreToolUse + PostToolUse together add ~200–600ms of latency per tool call.
- **State file leaks**: Start-time state is coordinated via `~/.cache/claude-hooks/` (files
  named `claude_hook_*`, `claude_perm_*`, `claude_turn_*`, `claude_compact_*`). The directory
  is created with mode `0700`; files are written with `O_NOFOLLOW` to resist symlink attacks.
  If a hook crashes mid-turn, orphaned files are not cleaned up — they are small and harmless
  but accumulate over time.
- **OTLP protocol mismatch**: `settings.json` sets `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` but the
  hooks import `opentelemetry.exporter.otlp.proto.http.trace_exporter` (HTTP/protobuf). The env
  var is ignored; all spans use HTTP/protobuf regardless.
- **Model fallback**: `gen_ai.request.model` is read from the Stop event payload first, then
  `ANTHROPIC_MODEL` env. If neither is set (older Claude Code builds), the attribute is empty
  and cost queries will not work.

## Files

```
.claude/
├── install.py                 ← copies hooks, merges settings.json
├── settings.json              ← hook wiring + OTel env vars
└── hooks/
    ├── otel_span.py           ← shared OTLP emitter (imported by all hooks)
    ├── hook_session_start.py
    ├── hook_session_end.py
    ├── hook_user_prompt_submit.py
    ├── hook_pre_tool_use.py   ← writes start-time state file only; no span
    ├── hook_post_tool_use.py
    ├── hook_post_tool_use_failure.py
    ├── hook_stop.py
    ├── hook_stop_failure.py
    ├── hook_subagent_start.py
    ├── hook_subagent_stop.py
    ├── hook_pre_compact.py
    ├── hook_post_compact.py
    ├── hook_permission_request.py
    ├── hook_permission_denied.py
    ├── hook_notification.py   ← emits only for permission_prompt and idle_prompt
    ├── hook_cwd_changed.py    ← disabled in settings.json
    ├── hook_post_tool_batch.py ← disabled in settings.json
    └── dev/
        └── dump_env.py        ← dev tool: dump subprocess env vars (not a configured hook)
```
