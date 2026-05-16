#!/usr/bin/env python3
"""
hook_pre_compact.py
Fires before context compaction (auto or manual).
Records context window size before content is summarised.

stdin fields:
  session_id, cwd, hook_event_name
  trigger: "manual" | "auto"
  context_size_tokens (approximate tokens before compaction)
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
session_id = data.get("session_id", "")

# Persist for PostCompact to calculate savings
ts_file = os.path.join(tempfile.gettempdir(), f"claude_compact_{session_id}.pre")
with open(ts_file, "w") as f:
    import json
    json.dump({"ts": now, "tokens": data.get("context_size_tokens", 0)}, f)

emit_span(
    "claude_code.context.pre_compact",
    {
        "session.id":                  session_id,
        "cwd":                         data.get("cwd", ""),
        "compaction.trigger":          data.get("trigger", "auto"),
        "context.tokens_before":       data.get("context_size_tokens", 0),
    },
    start_time_ns=now,
    end_time_ns=now,
)
