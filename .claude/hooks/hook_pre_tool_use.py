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
import time
from otel_span import read_stdin, write_state

data        = read_stdin()
now         = time.time_ns()
tool_use_id = data.get("tool_use_id", data.get("tool_name", "unknown"))
session_id  = data.get("session_id", "")

# Persist start time so PostToolUse / PostToolUseFailure can compute duration
write_state(f"claude_hook_{session_id}_{tool_use_id}.start", str(now))
