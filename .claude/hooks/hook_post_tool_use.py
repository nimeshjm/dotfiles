#!/usr/bin/env python3
"""
hook_post_tool_use.py
Fires after a tool call succeeds. Emits a span covering the full tool duration
by reading the start timestamp written by hook_pre_tool_use.py.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input, tool_response, duration_ms
"""
import os, time, json
from otel_span import read_stdin, emit_span, pop_state_int, read_state, tool_attrs

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")

# Start time written by PreToolUse; turn_id written by UserPromptSubmit
# (turn_id is read without deleting — Stop clears it at end of turn)
start_ns = pop_state_int(f"claude_hook_{session_id}_{tool_use_id}.start", now)
turn_id  = read_state(f"claude_turn_{session_id}.id")

attrs = {
    "session.id":            session_id,
    "cwd":                   data.get("cwd", ""),
    "turn.id":               turn_id,
    "gen_ai.operation.name": "tool_call",
    **tool_attrs(tool_name),
    "gen_ai.tool.success":   True,
    "tool_use_id":           tool_use_id,
}

# Lines changed for Edit/Write — proxy for code output volume
tool_input_data = data.get("tool_input", {}) or {}
if tool_name == "Edit":
    old = tool_input_data.get("old_string", "")
    new = tool_input_data.get("new_string", "")
    attrs["edit.lines_removed"] = len(old.splitlines())
    attrs["edit.lines_added"]   = len(new.splitlines())
elif tool_name == "Write":
    content = tool_input_data.get("content", "")
    attrs["write.lines"] = len(content.splitlines())

if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(tool_input_data)[:2000]

if os.environ.get("OTEL_LOG_TOOL_CONTENT") == "1":
    response = data.get("tool_response", "")
    attrs["gen_ai.tool.output"] = str(response)[:2000]

emit_span("claude_code.tool", attrs, start_time_ns=start_ns, end_time_ns=now)
