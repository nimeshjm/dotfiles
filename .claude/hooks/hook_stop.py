#!/usr/bin/env python3
"""
hook_stop.py
Fires each time Claude finishes responding (once per turn).
Records stop reason, token usage, model, and turn linkage, then posts a turn
summary to the Jira ticket named in the git branch (see jira_comment.py).
Also emits one claude_code.llm_call child span per API response in the turn.

stdin fields (Claude Code Stop hook payload — does NOT include model/usage/stop_reason):
  session_id, cwd, hook_event_name, transcript_path

model, stop_reason, and usage are read from assistant entries in the
transcript JSONL (transcript_path), since the Stop payload omits them.
"""
import os, time, json
from otel_span import read_stdin, emit_spans, pop_state, pop_state_int, write_state, log_debug
from transcript import last_assistant_message, turn_llm_calls
import jira_comment

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")
log_debug(f"invoked session={session_id} endpoint={os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', 'NOT_SET')}")

# Capture raw payload for debugging: cat ~/.cache/claude-hooks/last_stop_payload.json
write_state("last_stop_payload.json", json.dumps(data, indent=2))

transcript_path = data.get("transcript_path", "")
log_debug(f"reading transcript path={transcript_path!r} exists={os.path.exists(transcript_path)}")

# Read and clear the turn_id + turn start written by UserPromptSubmit.
# pop (not read): if StopFailure already claimed the root, fall back to standalone.
turn_id       = pop_state(f"claude_turn_{session_id}.id")
turn_start_ns = pop_state_int(f"claude_turn_{session_id}.start_ns", now)

# One record per deduped assistant API response in this turn
llm_calls = turn_llm_calls(transcript_path)
log_debug(f"turn_llm_calls: {len(llm_calls)} deduped responses")

# Fallback for model/stop_reason when the transcript walk found nothing
fallback    = last_assistant_message(transcript_path)
model       = (llm_calls[-1]["model"] if llm_calls else fallback["model"]) or os.environ.get("ANTHROPIC_MODEL", "")
stop_reason = llm_calls[-1]["stop_reason"] if llm_calls else fallback["stop_reason"]

# Token sums across ALL API responses in the turn (fixes undercounting)
def _sum(key: str) -> int:
    if llm_calls:
        return sum(c["usage"].get(key, 0) or 0 for c in llm_calls)
    return (fallback["usage"] or {}).get(key, 0)

input_tokens   = _sum("input_tokens")
output_tokens  = _sum("output_tokens")
cache_read     = _sum("cache_read_input_tokens")
cache_creation = _sum("cache_creation_input_tokens")

# Pre-compute cache hit ratio so dashboards don't need derived columns
cache_total     = cache_read + input_tokens
cache_hit_ratio = round(cache_read / cache_total, 4) if cache_total > 0 else 0.0

common = {"session.id": session_id, "cwd": data.get("cwd", ""), "turn.id": turn_id}

specs = [{
    "name": "claude_code.turn.stop",
    "attributes": {
        **common,
        "gen_ai.operation.name":              "chat",
        "gen_ai.request.model":               model,
        "agent.stop_reason":                  stop_reason,
        "turn.llm_calls":                     len(llm_calls),
        "gen_ai.usage.input_tokens":          input_tokens,
        "gen_ai.usage.output_tokens":         output_tokens,
        "gen_ai.usage.cache_creation_tokens": cache_creation,
        "gen_ai.usage.cache_read_tokens":     cache_read,
        "gen_ai.usage.cache_hit_ratio":       cache_hit_ratio,
    },
    "start_time_ns": turn_start_ns,
    "end_time_ns":   now,
    "status_ok":     stop_reason not in ("error", "max_turns"),
    "error_message": stop_reason if stop_reason in ("error", "max_turns") else "",
    "error_type":    "stop_reason",
    "turn_role":     "root",
}]

for call in llm_calls:
    usage    = call["usage"]
    end_ns   = call["end_ns"] or now
    start_ns = call["start_ns"] or turn_start_ns
    if start_ns > end_ns:
        start_ns = end_ns
    specs.append({
        "name": "claude_code.llm_call",
        "attributes": {
            **common,
            "gen_ai.operation.name":              "chat",
            "gen_ai.request.model":               call["model"],
            "gen_ai.response.id":                 call["message_id"],
            "agent.stop_reason":                  call["stop_reason"],
            "gen_ai.usage.input_tokens":          usage.get("input_tokens", 0),
            "gen_ai.usage.output_tokens":         usage.get("output_tokens", 0),
            "gen_ai.usage.cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
            "gen_ai.usage.cache_read_tokens":     usage.get("cache_read_input_tokens", 0),
        },
        "start_time_ns": start_ns,
        "end_time_ns":   end_ns,
        "turn_role":     "child",
    })

log_debug(f"emit_spans n={len(specs)} session={session_id} turn={turn_id}")
emit_spans(specs, session_id=session_id, turn_id=turn_id)
log_debug("emit_spans complete")

# Jira comes after span emission so a Jira failure can never lose the span
jira_comment.post_turn_summary(data.get("cwd", ""), transcript_path)
