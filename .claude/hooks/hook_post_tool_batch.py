#!/usr/bin/env python3
"""
hook_post_tool_batch.py
Fires after a full batch of parallel tool calls resolves, before the next
model call. Useful for recording aggregate batch stats.

stdin fields:
  session_id, cwd, hook_event_name
  tool_results: list of { tool_name, tool_use_id, success, duration_ms }
"""
import sys, os, time, json
sys.path.insert(0, os.path.expanduser(os.path.dirname(os.path.abspath(__file__))))
from otel_span import read_stdin, emit_span

data         = read_stdin()
now          = time.time_ns()
tool_results = data.get("tool_results", []) or []
session_id   = data.get("session_id", "")

total        = len(tool_results)
failures     = sum(1 for r in tool_results if not r.get("success", True))
max_duration = max((r.get("duration_ms", 0) for r in tool_results), default=0)
tool_names   = ",".join(r.get("tool_name", "") for r in tool_results)

emit_span(
    "claude_code.tool_batch",
    {
        "session.id":              session_id,
        "cwd":                     data.get("cwd", ""),
        "gen_ai.operation.name":   "post_tool_batch",
        "batch.tool_count":        total,
        "batch.failure_count":     failures,
        "batch.max_duration_ms":   max_duration,
        "batch.tool_names":        tool_names[:500],
    },
    start_time_ns=now,
    end_time_ns=now,
    status_ok=failures == 0,
    error_message=f"{failures} tool(s) failed in batch" if failures else "",
)
