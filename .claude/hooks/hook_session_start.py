#!/usr/bin/env python3
"""
hook_session_start.py
Fires once when a Claude Code session begins or resumes.

stdin fields:
  session_id, cwd, hook_event_name
  trigger: "startup" | "resume" | "clear" | "compact"
"""
import time
from otel_span import read_stdin, emit_span, write_state

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")

if session_id:
    write_state(f"claude_session_{session_id}.start_ns", str(now))

emit_span(
    "claude_code.session.start",
    {
        "session.id":      session_id,
        "session.trigger": data.get("trigger", "startup"),
        "cwd":             data.get("cwd", ""),
    },
    start_time_ns=now,
    end_time_ns=now + 1_000_000,
)
