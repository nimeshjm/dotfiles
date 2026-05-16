#!/usr/bin/env python3
"""
hook_cwd_changed.py
Fires whenever the working directory changes (e.g. Claude runs a `cd` command).
Useful for tracking which parts of a monorepo the agent is working in.

stdin fields:
  session_id, cwd (new directory), hook_event_name
  previous_cwd (the directory before the change)
"""
import sys, os, time
sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))
from otel_span import read_stdin, emit_span

data = read_stdin()
now  = time.time_ns()

emit_span(
    "claude_code.cwd_changed",
    {
        "session.id":            data.get("session_id", ""),
        "cwd":                   data.get("cwd", ""),
        "cwd.previous":          data.get("previous_cwd", ""),
        "gen_ai.operation.name": "cwd_changed",
    },
    start_time_ns=now,
    end_time_ns=now,
)
