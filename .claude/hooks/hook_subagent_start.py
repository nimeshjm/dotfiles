#!/usr/bin/env python3
"""
hook_subagent_start.py
Fires when a subagent is spawned (via the Task tool or agent teams).

stdin fields:
  session_id, cwd, hook_event_name
  agent_id, agent_type: "general-purpose" | "Explore" | "Plan" | custom name
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
agent_id   = data.get("agent_id", "")
session_id = data.get("session_id", "")

# Persist start time for SubagentStop duration
start_file = os.path.join(tempfile.gettempdir(), f"claude_subagent_{session_id}_{agent_id}.start")
with open(start_file, "w") as f:
    f.write(str(now))

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
