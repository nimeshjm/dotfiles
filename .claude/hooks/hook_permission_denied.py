#!/usr/bin/env python3
"""
hook_permission_denied.py
Fires when a tool call is denied by the auto-mode classifier.
Measures how long the permission gate took by looking up the timestamp
written by hook_permission_request.py.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input
  deny_reason (optional)
"""
import time
from otel_span import read_stdin, emit_span, pop_state_int, read_state

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")
turn_id     = read_state(f"claude_turn_{session_id}.id")  # Join the current turn's trace if one is active (read, don't consume)

start_ns = pop_state_int(f"claude_perm_{session_id}_{tool_use_id}.ts", now)

emit_span(
    "claude_code.permission.denied",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "turn.id":                 turn_id,
        "gen_ai.tool.name":        tool_name,
        "tool_use_id":             tool_use_id,
        "permission.denied":       True,
        "permission.deny_reason":  data.get("deny_reason", ""),
    },
    start_time_ns=start_ns,
    end_time_ns=now,
    status_ok=False,
    error_message="permission denied",
    session_id=session_id,
    turn_id=turn_id,
)
