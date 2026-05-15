#!/usr/bin/env python3
"""
hook_permission_request.py
Fires when a permission dialog appears (interactive mode only).
Records which tool triggered the request and persists timestamp so we
can measure how long the human took to decide (in PermissionDenied or the
next PostToolUse).

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input
  permission_mode: current mode at time of request
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")

# Persist so we can compute human-decision latency downstream
perm_file = os.path.join(tempfile.gettempdir(), f"claude_perm_{session_id}_{tool_use_id}.ts")
with open(perm_file, "w") as f:
    f.write(str(now))

emit_span(
    "claude_code.permission.request",
    {
        "session.id":            session_id,
        "cwd":                   data.get("cwd", ""),
        "gen_ai.tool.name":      tool_name,
        "tool_use_id":           tool_use_id,
        "permission.mode":       data.get("permission_mode", ""),
    },
    start_time_ns=now,
    end_time_ns=now,
)
