#!/usr/bin/env python3
"""
hook_stop_failure.py
Fires when the turn ends due to an API error.
Note: Claude Code ignores your stdout/exit code here — this hook is purely
for side effects (logging, alerting).

stdin fields:
  session_id, cwd, hook_event_name
  error_type: "rate_limit" | "authentication_failed" | "billing_error" |
              "invalid_request" | "server_error" | "max_output_tokens" | "unknown"
"""
import time
from otel_span import read_stdin, emit_span, pop_state, pop_state_int

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")
error_type = data.get("error_type", "unknown")

# Claim and clear the turn state: if Stop never fires, this span IS the turn root.
turn_id       = pop_state(f"claude_turn_{session_id}.id")
turn_start_ns = pop_state_int(f"claude_turn_{session_id}.start_ns", now)

emit_span(
    "claude_code.turn.stop_failure",
    {
        "session.id": session_id,
        "cwd":        data.get("cwd", ""),
        "turn.id":    turn_id,
        "error.type": error_type,
    },
    start_time_ns=turn_start_ns,
    end_time_ns=now,
    status_ok=False,
    error_message=error_type,
    error_type=error_type,
    session_id=session_id,
    turn_id=turn_id,
    turn_role="root",
)
