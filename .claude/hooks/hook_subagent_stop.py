#!/usr/bin/env python3
"""
hook_subagent_stop.py
Fires when a subagent finishes. Emits a span covering the full subagent lifetime.

stdin fields:
  session_id, cwd, hook_event_name
  agent_id, agent_type
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
agent_id   = data.get("agent_id", "")
session_id = data.get("session_id", "")

start_ns = now
start_file = os.path.join(tempfile.gettempdir(), f"claude_subagent_{session_id}_{agent_id}.start")
if os.path.exists(start_file):
    try:
        with open(start_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(start_file)
    except (ValueError, OSError):
        pass

duration_ms = (now - start_ns) // 1_000_000

emit_span(
    "claude_code.subagent",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "gen_ai.operation.name":   "subagent",
        "agent.id":                agent_id,
        "agent.type":              data.get("agent_type", "general-purpose"),
        "agent.duration_ms":       duration_ms,
    },
    start_time_ns=start_ns,
    end_time_ns=now,
)
