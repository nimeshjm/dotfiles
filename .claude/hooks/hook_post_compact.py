#!/usr/bin/env python3
"""
hook_post_compact.py
Fires after context compaction completes.
Computes token savings by comparing against the pre-compact snapshot.

stdin fields:
  session_id, cwd, hook_event_name
  trigger: "manual" | "auto"
  context_size_tokens (tokens after compaction)
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")
tokens_after = data.get("context_size_tokens", 0)

start_ns    = now
tokens_before = 0

ts_file = _state_path(f"claude_compact_{session_id}.pre")
if os.path.exists(ts_file):
    try:
        with open(ts_file) as f:
            pre = json.load(f)
        start_ns      = int(pre.get("ts", now))
        tokens_before = int(pre.get("tokens", 0))
        os.unlink(ts_file)
    except (ValueError, KeyError, OSError):
        pass

tokens_saved = max(0, tokens_before - tokens_after)

emit_span(
    "claude_code.context.compact",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "compaction.trigger":      data.get("trigger", "auto"),
        "context.tokens_before":   tokens_before,
        "context.tokens_after":    tokens_after,
        "context.tokens_saved":    tokens_saved,
        "compaction.duration_ms":  (now - start_ns) // 1_000_000,
    },
    start_time_ns=start_ns,
    end_time_ns=now,
)
