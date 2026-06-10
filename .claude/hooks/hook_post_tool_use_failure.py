#!/usr/bin/env python3
"""
hook_post_tool_use_failure.py
Fires after a tool call fails. Emits an error span.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input, error, duration_ms
"""
import os, time, json
from otel_span import read_stdin, emit_span, pop_state_int, read_state, tool_attrs

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")
error       = str(data.get("error", "unknown error"))

# Start time written by PreToolUse; turn_id written by UserPromptSubmit
start_ns = pop_state_int(f"claude_hook_{session_id}_{tool_use_id}.start", now)
turn_id  = read_state(f"claude_turn_{session_id}.id")

attrs = {
    "session.id":            session_id,
    "cwd":                   data.get("cwd", ""),
    "turn.id":               turn_id,
    "gen_ai.operation.name": "tool_call",
    **tool_attrs(tool_name),
    "gen_ai.tool.success":   False,
    "tool_use_id":           tool_use_id,
    "error.message":         error[:500],
    "error.type":            data.get("error_type", "unknown"),
}

if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(data.get("tool_input", {}))[:2000]

emit_span(
    "claude_code.tool",
    attrs,
    start_time_ns=start_ns,
    end_time_ns=now,
    status_ok=False,
    error_message=error,
)
