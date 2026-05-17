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
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")

start_ns = now
perm_file = _state_path(f"claude_perm_{session_id}_{tool_use_id}.ts")
if os.path.exists(perm_file):
    try:
        with open(perm_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(perm_file)
    except (ValueError, OSError):
        pass

emit_span(
    "claude_code.permission.denied",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "gen_ai.tool.name":        tool_name,
        "tool_use_id":             tool_use_id,
        "permission.denied":       True,
        "permission.deny_reason":  data.get("deny_reason", ""),
        "permission.decision_ms":  (now - start_ns) // 1_000_000,
    },
    start_time_ns=start_ns,
    end_time_ns=now,
    status_ok=False,
    error_message="permission denied",
)
