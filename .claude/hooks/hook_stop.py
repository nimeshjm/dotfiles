#!/usr/bin/env python3
"""
hook_stop.py
Fires each time Claude finishes responding (once per turn).
Records stop reason, token usage, model, and turn linkage.

stdin fields:
  session_id, cwd, hook_event_name
  stop_reason: "end_turn" | "max_tokens" | "tool_use" | etc.
  model: model ID for this turn (e.g. "claude-sonnet-4-6")
  usage: { input_tokens, output_tokens, cache_creation_input_tokens,
           cache_read_input_tokens }
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data        = read_stdin()
now         = time.time_ns()
usage       = data.get("usage", {}) or {}
stop_reason = data.get("stop_reason", "unknown")
session_id  = data.get("session_id", "")

# Read and clear the turn_id written by UserPromptSubmit
turn_id   = ""
turn_file = os.path.join(tempfile.gettempdir(), f"claude_turn_{session_id}.id")
if os.path.exists(turn_file):
    try:
        with open(turn_file) as f:
            turn_id = f.read().strip()
        os.unlink(turn_file)
    except OSError:
        pass

# Model: payload first, env var fallback
model = data.get("model", "") or os.environ.get("ANTHROPIC_MODEL", "")

# Pre-compute cache hit ratio so dashboards don't need derived columns
cache_read   = usage.get("cache_read_input_tokens", 0)
input_tokens = usage.get("input_tokens", 0)
cache_total  = cache_read + input_tokens
cache_hit_ratio = round(cache_read / cache_total, 4) if cache_total > 0 else 0.0

emit_span(
    "claude_code.turn.stop",
    {
        "session.id":                         session_id,
        "cwd":                                data.get("cwd", ""),
        "turn.id":                            turn_id,
        "gen_ai.operation.name":              "chat",
        "gen_ai.request.model":               model,
        "agent.stop_reason":                  stop_reason,
        "gen_ai.usage.input_tokens":          input_tokens,
        "gen_ai.usage.output_tokens":         usage.get("output_tokens", 0),
        "gen_ai.usage.cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "gen_ai.usage.cache_read_tokens":     cache_read,
        "gen_ai.usage.cache_hit_ratio":       cache_hit_ratio,
    },
    start_time_ns=now,
    end_time_ns=now,
    status_ok=stop_reason not in ("error", "max_turns"),
    error_message=stop_reason if stop_reason in ("error", "max_turns") else "",
)
