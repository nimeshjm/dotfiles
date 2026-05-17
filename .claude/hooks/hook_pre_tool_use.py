#!/usr/bin/env python3
"""
hook_pre_tool_use.py
Fires before every tool call.
Writes a start-time tempfile so hook_post_tool_use.py can compute duration.
No span is emitted here — PostToolUse emits the full-duration tool span.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input (dict — contents vary by tool)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, _open_state_file

data        = read_stdin()
now         = time.time_ns()
tool_use_id = data.get("tool_use_id", data.get("tool_name", "unknown"))
session_id  = data.get("session_id", "")

# Persist start time so PostToolUse / PostToolUseFailure can compute duration
try:
    with _open_state_file(f"claude_hook_{session_id}_{tool_use_id}.start") as f:
        f.write(str(now))
except OSError:
    pass
