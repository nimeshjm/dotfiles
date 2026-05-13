# Claude Code OTel hooks

Full OpenTelemetry instrumentation for interactive Claude Code sessions,
via shell-command hooks. Covers every hook event the CLI exposes.

## Install

```bash
# 1. Copy the .claude/ folder into your project root (or ~/.claude/ for global)
cp -r .claude/ /your/project/.claude/

# 2. Install OTel Python packages (once per machine)
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

# 3. Edit .claude/settings.json
#    Replace YOUR_API_KEY with your Honeycomb ingest key.
#    Or swap OTEL_EXPORTER_OTLP_ENDPOINT for any OTLP backend.
```

## Spans emitted

| Hook event | Span name | Key attributes |
|---|---|---|
| SessionStart | `claude_code.session.start` | session.id, trigger |
| SessionEnd | `claude_code.session.end` | session.id, end_reason |
| UserPromptSubmit | `claude_code.user_prompt` | prompt.char_length, prompt.word_count |
| PreToolUse | `claude_code.tool.pre` | tool.name, tool.type, tool_use_id |
| PostToolUse | `claude_code.tool` | tool.name, success=true, duration_ms |
| PostToolUseFailure | `claude_code.tool` | tool.name, success=false, error.message |
| PostToolBatch | `claude_code.tool_batch` | batch.tool_count, failure_count, max_duration_ms |
| Stop | `claude_code.turn.stop` | stop_reason, token counts |
| StopFailure | `claude_code.turn.stop_failure` | error.type |
| SubagentStart | `claude_code.subagent.start` | agent.id, agent.type |
| SubagentStop | `claude_code.subagent` | agent.id, duration_ms |
| PreCompact | `claude_code.context.pre_compact` | tokens_before, trigger |
| PostCompact | `claude_code.context.compact` | tokens_before, tokens_after, tokens_saved |
| PermissionRequest | `claude_code.permission.request` | tool.name, permission.mode |
| PermissionDenied | `claude_code.permission.denied` | tool.name, deny_reason, decision_ms |
| Notification | `claude_code.notification` | notification.type, message |
| CwdChanged | `claude_code.cwd_changed` | cwd, cwd.previous |

## Opt-in content capture

Set in `.claude/settings.json` env section before enabling in production:

| Env var | What it adds |
|---|---|
| `OTEL_LOG_USER_PROMPTS=1` | Prompt text (first 2000 chars) on `user_prompt` spans |
| `OTEL_LOG_TOOL_DETAILS=1` | Tool input args on `tool.pre` and `tool` spans |
| `OTEL_LOG_TOOL_CONTENT=1` | Tool output on `tool` spans |

## Testing a single hook manually

```bash
echo '{"session_id":"test123","cwd":"/tmp","tool_name":"Bash","tool_use_id":"tu_1","tool_input":{"command":"ls"}}' \
  | python3 .claude/hooks/hook_pre_tool_use.py
```

## Applying globally (all projects)

Put the hooks in `~/.claude/hooks/` and configure `~/.claude/settings.json`.
Use absolute paths in the command fields instead of `${CLAUDE_PROJECT_DIR}`.

## Files

```
.claude/
├── settings.json              ← hook wiring + OTel env vars
└── hooks/
    ├── otel_span.py           ← shared OTLP emitter (imported by all hooks)
    ├── hook_session_start.py
    ├── hook_session_end.py
    ├── hook_user_prompt_submit.py
    ├── hook_pre_tool_use.py
    ├── hook_post_tool_use.py
    ├── hook_post_tool_use_failure.py
    ├── hook_post_tool_batch.py
    ├── hook_stop.py
    ├── hook_stop_failure.py
    ├── hook_subagent_start.py
    ├── hook_subagent_stop.py
    ├── hook_pre_compact.py
    ├── hook_post_compact.py
    ├── hook_permission_request.py
    ├── hook_permission_denied.py
    ├── hook_notification.py
    └── hook_cwd_changed.py
```
