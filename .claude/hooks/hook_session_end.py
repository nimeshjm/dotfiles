#!/usr/bin/env python3
"""
hook_session_end.py
Fires once when a Claude Code session terminates.

stdin fields:
  session_id, cwd, hook_event_name
  reason: "clear" | "resume" | "logout" | "prompt_input_exit" |
          "bypass_permissions_disabled" | "other"
"""
import time
from otel_span import read_stdin, emit_span, pop_state_int

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")

session_duration_ms = 0
if session_id:
    start_ns = pop_state_int(f"claude_session_{session_id}.start_ns", now)
    session_duration_ms = (now - start_ns) // 1_000_000

emit_span(
    "claude_code.session.end",
    {
        "session.id":          session_id,
        "session.end_reason":  data.get("reason", "other"),
        "session.duration_ms": session_duration_ms,
        "cwd":                 data.get("cwd", ""),
    },
    start_time_ns=now,
    end_time_ns=now + 1_000_000,
)
