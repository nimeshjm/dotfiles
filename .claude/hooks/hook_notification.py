#!/usr/bin/env python3
"""
hook_notification.py
Fires on agent status notifications.
Lightweight: emits a point-in-time span (not a duration) for each event.

notification_type values:
  permission_prompt    — Claude needs permission to use a tool
  idle_prompt          — Claude is waiting for your input (>60s idle)
  auth_success         — authentication succeeded
  elicitation_dialog   — MCP server is requesting user input
  elicitation_complete — MCP elicitation finished
  elicitation_response — user responded to MCP elicitation

stdin fields:
  session_id, cwd, hook_event_name
  notification_type, message (optional)
"""
import sys, time
from otel_span import read_stdin, emit_span, read_state

data              = read_stdin()
now               = time.time_ns()
notification_type = data.get("notification_type", "")
message           = data.get("message", "")
session_id        = data.get("session_id", "")
turn_id           = read_state(f"claude_turn_{session_id}.id")  # Join the current turn's trace if one is active (read, don't consume)

# Only track actionable / high-value notification types
if notification_type not in ("permission_prompt", "idle_prompt"):
    sys.exit(0)

emit_span(
    "claude_code.notification",
    {
        "session.id":           session_id,
        "cwd":                  data.get("cwd", ""),
        "turn.id":              turn_id,
        "notification.type":    notification_type,
        "notification.message": message[:500],
    },
    start_time_ns=now,
    end_time_ns=now,
    session_id=session_id,
    turn_id=turn_id,
)
