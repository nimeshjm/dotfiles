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
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
error_type = data.get("error_type", "unknown")

emit_span(
    "claude_code.turn.stop_failure",
    {
        "session.id":            data.get("session_id", ""),
        "cwd":                   data.get("cwd", ""),
        "error.type":            error_type,
    },
    start_time_ns=now,
    end_time_ns=now,
    status_ok=False,
    error_message=error_type,
)
