#!/usr/bin/env python3
"""
hook_subagent_stop.py
Fires when a subagent finishes. Emits a span covering the full subagent lifetime.

stdin fields:
  session_id, cwd, hook_event_name
  agent_id, agent_type
"""
import time
from otel_span import read_stdin, emit_span, pop_state_int

data       = read_stdin()
now        = time.time_ns()
agent_id   = data.get("agent_id", "")
session_id = data.get("session_id", "")

start_ns    = pop_state_int(f"claude_subagent_{session_id}_{agent_id}.start", now)
duration_ms = (now - start_ns) // 1_000_000

emit_span(
    "claude_code.subagent",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "agent.id":                agent_id,
        "agent.type":              data.get("agent_type", "general-purpose"),
        "agent.duration_ms":       duration_ms,
    },
    start_time_ns=start_ns,
    end_time_ns=now,
)
