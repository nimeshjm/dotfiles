#!/usr/bin/env python3
"""
hook_session_end.py
Fires once when a Claude Code session terminates.

stdin fields:
  session_id, cwd, hook_event_name
  reason: "clear" | "resume" | "logout" | "prompt_input_exit" |
          "bypass_permissions_disabled" | "other"
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data = read_stdin()
now  = time.time_ns()

emit_span(
    "claude_code.session.end",
    {
        "session.id":           data.get("session_id", ""),
        "session.end_reason":   data.get("reason", "other"),
        "cwd":                  data.get("cwd", ""),
        "gen_ai.operation.name":"session_end",
    },
    start_time_ns=now,
    end_time_ns=now,
)
