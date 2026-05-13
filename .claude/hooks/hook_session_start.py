#!/usr/bin/env python3
"""
hook_session_start.py
Fires once when a Claude Code session begins or resumes.

stdin fields:
  session_id, cwd, hook_event_name
  trigger: "startup" | "resume" | "clear" | "compact"
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data = read_stdin()
now  = time.time_ns()

emit_span(
    "claude_code.session.start",
    {
        "session.id":           data.get("session_id", ""),
        "session.trigger":      data.get("trigger", "startup"),
        "cwd":                  data.get("cwd", ""),
        "gen_ai.operation.name":"session_start",
    },
    start_time_ns=now,
    end_time_ns=now,
)
