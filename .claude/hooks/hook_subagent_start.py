#!/usr/bin/env python3
"""
hook_subagent_start.py
Fires when a subagent is spawned (via the Task tool or agent teams).

stdin fields:
  session_id, cwd, hook_event_name
  agent_id, agent_type: "general-purpose" | "Explore" | "Plan" | custom name
"""
import time
from otel_span import read_stdin, emit_span, write_state

data       = read_stdin()
now        = time.time_ns()
agent_id   = data.get("agent_id", "")
session_id = data.get("session_id", "")

# Persist start time for SubagentStop duration
write_state(f"claude_subagent_{session_id}_{agent_id}.start", str(now))

emit_span(
    "claude_code.subagent.start",
    {
        "session.id":            session_id,
        "cwd":                   data.get("cwd", ""),
        "agent.id":              agent_id,
        "agent.type":            data.get("agent_type", "general-purpose"),
    },
    start_time_ns=now,
    end_time_ns=now,
)
