#!/usr/bin/env python3
"""
hook_stop.py
Fires each time Claude finishes responding (once per turn).
Records stop reason, token usage, model, and turn linkage, then posts a turn
summary to the Jira ticket named in the git branch (see jira_comment.py).

stdin fields (Claude Code Stop hook payload — does NOT include model/usage/stop_reason):
  session_id, cwd, hook_event_name, transcript_path

model, stop_reason, and usage are read from the last assistant entry in the
transcript JSONL (transcript_path), since the Stop payload omits them.
"""
import os, time, json
from otel_span import read_stdin, emit_span, pop_state, write_state, log_debug
from transcript import last_assistant_message
import jira_comment

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")
log_debug(f"invoked session={session_id} endpoint={os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', 'NOT_SET')}")

# Capture raw payload for debugging: cat ~/.cache/claude-hooks/last_stop_payload.json
write_state("last_stop_payload.json", json.dumps(data, indent=2))

transcript_path = data.get("transcript_path", "")
log_debug(f"reading transcript path={transcript_path!r} exists={os.path.exists(transcript_path)}")
assistant   = last_assistant_message(transcript_path)
usage       = assistant["usage"] or {}
stop_reason = assistant["stop_reason"]
model       = assistant["model"] or os.environ.get("ANTHROPIC_MODEL", "")
log_debug(f"transcript result: model={model!r} stop_reason={stop_reason!r} input_tokens={usage.get('input_tokens', 0)}")

# Read and clear the turn_id written by UserPromptSubmit
turn_id = pop_state(f"claude_turn_{session_id}.id")

# Pre-compute cache hit ratio so dashboards don't need derived columns
cache_read   = usage.get("cache_read_input_tokens", 0)
input_tokens = usage.get("input_tokens", 0)
cache_total  = cache_read + input_tokens
cache_hit_ratio = round(cache_read / cache_total, 4) if cache_total > 0 else 0.0

log_debug(f"calling emit_span session={session_id} turn={turn_id}")
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
log_debug("emit_span complete")

# Jira comes after span emission so a Jira failure can never lose the span
jira_comment.post_turn_summary(data.get("cwd", ""), transcript_path)
