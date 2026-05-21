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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")

session_duration_ms = 0
start_file = _state_path(f"claude_session_{session_id}.start_ns")
if session_id and os.path.exists(start_file):
    try:
        with open(start_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(start_file)
        session_duration_ms = (now - start_ns) // 1_000_000
    except (ValueError, OSError):
        pass

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
