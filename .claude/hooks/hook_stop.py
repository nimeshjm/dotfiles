#!/usr/bin/env python3
"""
hook_stop.py
Fires each time Claude finishes responding (once per turn).
Records stop reason and token usage from the turn.

stdin fields:
  session_id, cwd, hook_event_name
  stop_reason: "end_turn" | "max_tokens" | "tool_use" | etc.
  usage: { input_tokens, output_tokens, cache_creation_input_tokens,
           cache_read_input_tokens }
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
usage      = data.get("usage", {}) or {}
stop_reason= data.get("stop_reason", "unknown")

emit_span(
    "claude_code.turn.stop",
    {
        "session.id":                        data.get("session_id", ""),
        "cwd":                               data.get("cwd", ""),
        "gen_ai.operation.name":             "stop",
        "agent.stop_reason":                 stop_reason,
        "gen_ai.usage.input_tokens":         usage.get("input_tokens", 0),
        "gen_ai.usage.output_tokens":        usage.get("output_tokens", 0),
        "gen_ai.usage.cache_creation_tokens":usage.get("cache_creation_input_tokens", 0),
        "gen_ai.usage.cache_read_tokens":    usage.get("cache_read_input_tokens", 0),
    },
    start_time_ns=now,
    end_time_ns=now,
    status_ok=stop_reason not in ("error", "max_turns"),
    error_message=stop_reason if stop_reason in ("error", "max_turns") else "",
)
