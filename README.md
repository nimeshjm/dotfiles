# Claude Code Telemetry & Observability

A comprehensive OpenTelemetry instrumentation suite for Claude Code, providing session-level telemetry (tokens, models, tool usage, performance) streamed to Honeycomb for observability dashboards and analysis.

## Installation

### Prerequisites

- Claude Code (CLI or desktop)
- Python 3.11+
- Honeycomb account with API key (ingest key, not management key)

### Quick Start

1. **Clone or copy this repo** to `~/.claude`:
   ```bash
   git clone https://github.com/nmanmohanlal/dotfiles.git ~/.claude
   ```

2. **Set your Honeycomb ingest key** in `~/.claude/settings.json`:
   ```json
   "env": {
     "OTEL_EXPORTER_OTLP_HEADERS": "x-honeycomb-team=YOUR_INGEST_KEY"
   }
   ```

3. **Run the installer**:
   ```bash
   python3 ~/.claude/install.py
   ```
   This will:
   - Copy hooks into `~/.claude/hooks/`
   - Merge settings into `~/.claude/settings.json` (preserving your local config)

4. **Verify in Honeycomb**:
   - Open [Honeycomb](https://ui.honeycomb.io)
   - Navigate to the `claude-code` dataset
   - Run a Claude Code session and watch spans arrive in real-time

### Configuration

**Environment Variables** (set in `settings.json` under `env`):

- `CLAUDE_CODE_ENABLE_TELEMETRY=1` — Enable telemetry (required)
- `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` — Enable enhanced metrics
- `OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io` — Honeycomb endpoint
- `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=YOUR_KEY` — Ingest key
- `OTEL_LOG_USER_PROMPTS=0` — Don't log user input (privacy)
- `OTEL_LOG_TOOL_DETAILS=0` — Don't log tool parameters
- `OTEL_LOG_TOOL_CONTENT=0` — Don't log tool output

## Creating the Honeycomb Dashboard

The board definition lives in `PANELS` in `install.py`. To create it, use Claude Code with the [Honeycomb MCP server](https://docs.honeycomb.io/honeycomb-mcp/) configured:

1. Open Claude Code in this repo
2. Prompt it:
   > Create a Honeycomb board called "Claude Code Sessions" based on the PANELS definition in install.py

Claude will read `install.py`, run each query against your `claude-code` dataset, and create the board via MCP — no management key required, only your ingest key.

## Architecture

### Event Flow

Claude Code emits lifecycle events → hooks intercept events → `otel_span.py` exports OpenTelemetry spans → Honeycomb ingests and indexes.

```
SessionStart → hook_session_start.py → emit_span("claude_code.session.start")
UserPromptSubmit → hook_user_prompt_submit.py → emit_span("claude_code.turn.start")
PreToolUse → hook_pre_tool_use.py → write turn_id to cache
PostToolUse → hook_post_tool_use.py → emit_span("claude_code.tool") with tool metadata
Stop → hook_stop.py → extract model/tokens from transcript, emit_span("claude_code.turn.stop")
SessionEnd → hook_session_end.py → emit_span("claude_code.session.end")
```

### Instrumented Events

| Hook File | Event | Span Name | Key Attributes |
|-----------|-------|-----------|-----------------|
| `hook_session_start.py` | SessionStart | `claude_code.session.start` | session_id, cwd |
| `hook_session_end.py` | SessionEnd | `claude_code.session.end` | session_id, duration_ms |
| `hook_user_prompt_submit.py` | UserPromptSubmit | `claude_code.turn.start` | session_id, turn_id, input_tokens |
| `hook_pre_tool_use.py` | PreToolUse | (caches turn_id) | session_id, turn_id |
| `hook_post_tool_use.py` | PostToolUse | `claude_code.tool` | session_id, turn_id, tool_name, tool_result_tokens |
| `hook_post_tool_use_failure.py` | PostToolUseFailure | `claude_code.tool_error` | session_id, turn_id, error |
| `hook_stop.py` | Stop | `claude_code.turn.stop` | session_id, turn_id, model, output_tokens, stop_reason |
| `hook_pre_compact.py` | PreCompact | `claude_code.compact.start` | session_id, turns_compacted |
| `hook_post_compact.py` | PostCompact | `claude_code.compact.end` | session_id, compacted_tokens |
| `hook_subagent_start.py` | SubagentStart | `claude_code.subagent.start` | session_id, subagent_type |
| `hook_subagent_stop.py` | SubagentStop | `claude_code.subagent.stop` | session_id, subagent_type, duration_ms |

### OpenTelemetry Pipeline

**`otel_span.py`** is the shared instrumentation library:
- Accepts span name and attributes dictionary
- Coerces all non-primitive values to JSON strings (for compatibility with Honeycomb string columns)
- Exports via HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT` using the OTLP gRPC wire protocol
- Handles retries and timeout (5s default)

**Span Attributes** are automatically enriched:
- `otel.library.name=claude-code`
- `service.name=claude-code` (from OTEL_SERVICE_NAME env var)
- Timestamp: UTC-based span start/end times

### Data Storage

- **Session transcripts**: `~/.claude/sessions/YYYY-MM-DD/*.jsonl` — append-only event log per session
- **Turn cache**: `~/.cache/claude-hooks/claude_turn_*.id` — temporary turn ID tracking during active sessions
- **Hook logs** (debug): `~/.cache/claude-hooks/hook_stop.log` — diagnostic output for troubleshooting

## Hooks Reference

### Core Hooks

#### `hook_session_start.py`
Fires at session start. Emits `claude_code.session.start` span with:
- `session_id`: UUID for the session
- `cwd`: working directory
- `effort`: effort level (e.g., "normal", "extended_thinking")

#### `hook_stop.py`
Fires at session stop. Extracts model name and token counts from session transcript and emits `claude_code.turn.stop` with:
- `session_id`, `turn_id`: identifying the turn
- `model`: LLM model name (e.g., "claude-opus-4-7")
- `input_tokens`, `output_tokens`: token consumption
- `stop_reason`: why the turn ended (e.g., "end_turn", "max_tokens")

**Implementation detail**: Reads session transcript JSONL backward to find the most recent assistant message with usage data, then emits the span.

#### `hook_post_tool_use.py`
Fires after each tool execution. Emits `claude_code.tool` span with:
- `tool_name`: name of the tool called (e.g., "Bash", "Read")
- `tool_input_tokens`: tokens in the tool call
- `tool_result_tokens`: tokens in the tool result
- `duration_ms`: tool execution time
- Optional error details if tool failed

#### `hook_pre_tool_use.py` + `hook_post_tool_use.py` (Timing Pattern)
Pre-tool hook writes turn_id to cache file. Post-tool hook reads it to correlate tool spans with turns. Cleaned up at session end.

### Supporting Hooks

- **`hook_session_end.py`** — Session lifecycle span
- **`hook_user_prompt_submit.py`** — Turn start, input token count
- **`hook_post_tool_use_failure.py`** — Captures tool errors with error message and stack
- **`hook_pre_compact.py` / `hook_post_compact.py`** — Context window compaction events
- **`hook_subagent_start.py` / `hook_subagent_stop.py`** — Subagent lifecycle
- **`hook_permission_request.py`, `hook_permission_denied.py`** — Permission events
- **`hook_cwd_changed.py`** — Working directory changes
- **`hook_notification.py`** — User notifications

## Extending

### Adding a New Hook

1. **Create the hook file** in `~/.claude/hooks/hook_EVENT_NAME.py`:
   ```python
   #!/usr/bin/env python3
   import json
   import sys
   from pathlib import Path
   
   sys.path.insert(0, str(Path(__file__).parent))
   from otel_span import emit_span
   
   def main():
       # Read hook payload from stdin
       payload = json.loads(sys.stdin.read())
       
       # Extract data
       session_id = payload.get("session_id")
       
       # Emit span
       emit_span("claude_code.my_event", {
           "session_id": session_id,
           "my_attribute": "value",
           "my_count": 42,
       })
   
   if __name__ == "__main__":
       main()
   ```

2. **Register in `settings.json`** under `hooks`:
   ```json
   "MyEvent": [
     {
       "hooks": [
         {
           "type": "command",
           "command": "python3 ~/.claude/hooks/hook_my_event.py",
           "timeout": 10
         }
       ]
     }
   ]
   ```

3. **Test**:
   - Trigger the event in Claude Code
   - Check `~/.cache/claude-hooks/` for any logs
   - Verify span appears in Honeycomb within 10 seconds

### Common Patterns

**Extracting data from transcript**:
```python
from pathlib import Path
import json

transcript_path = Path(payload["transcript_path"])
with open(transcript_path) as f:
    for line in reversed(f.readlines()):
        event = json.loads(line)
        if event.get("type") == "assistant":
            model = event.get("model")
            usage = event.get("usage", {})
            break
```

**Type coercion**: `otel_span.py` automatically stringifies complex types. For structured data, use JSON strings:
```python
emit_span("event", {
    "scalar": 42,  # int → int
    "text": "hello",  # str → str
    "list": json.dumps([1, 2, 3]),  # list → JSON string
    "dict": json.dumps({"key": "value"}),  # dict → JSON string
})
```

**Accessing environment variables**:
```python
import os
api_key = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://api.honeycomb.io")
```

## Troubleshooting

### No spans arriving in Honeycomb

**Check 1: Verify API key**
```bash
grep "x-honeycomb-team" ~/.claude/settings.json
# Should show your actual ingest key, not "YOUR_API_KEY"
```

**Check 2: Verify OTel dependencies**
```bash
python3 -c "import opentelemetry; print('✓ OTel installed')"
python3 -c "from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter; print('✓ OTLP HTTP exporter installed')"
```

**Check 3: Check hook logs** (if available):
```bash
cat ~/.cache/claude-hooks/hook_stop.log  # Last session's diagnostics
```

**Check 4: Verify hook execution** by adding debug output:
```bash
# Manually trigger a hook to see if it runs
echo '{"session_id": "test"}' | python3 ~/.claude/hooks/hook_session_start.py
```

### Sparse data in Honeycomb dashboard

- **Missing tokens**: `hook_stop.py` reads transcript to extract model/tokens. Ensure session transcript exists at the path provided.
- **Empty stop_reason**: Stop hook didn't receive the payload; check that Claude Code is invoking the hook.
- **String columns showing "unknown"**: Some attributes are coerced to strings; verify with raw span view in Honeycomb.

### Stale turn ID cache files

After abnormal termination, `~/.cache/claude-hooks/claude_turn_*.id` files may remain. Safe to delete:
```bash
rm ~/.cache/claude-hooks/claude_turn_*.id
```

### Settings merge conflicts

If `settings.json` merge overwrites your local config, manually inspect `~/.claude/settings.local.json` and re-merge with `~/.claude/settings.json`.

---

**Questions?** Check the session transcripts in `~/.claude/sessions/` or enable hook logging by adding `_log()` calls to hook scripts.
