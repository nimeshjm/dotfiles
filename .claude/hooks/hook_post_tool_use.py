#!/usr/bin/env python3
"""
hook_post_tool_use.py
Fires after a tool call succeeds. Emits a span that spans the full tool duration
by reading the start timestamp written by hook_pre_tool_use.py.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input, tool_response, duration_ms
"""
import sys, os, time, json, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")

# Retrieve the start time written by PreToolUse
start_ns = now
start_file = os.path.join(tempfile.gettempdir(), f"claude_hook_{session_id}_{tool_use_id}.start")
if os.path.exists(start_file):
    try:
        with open(start_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(start_file)
    except (ValueError, OSError):
        pass

is_mcp = tool_name.startswith("mcp__")
parts  = tool_name.split("__") if is_mcp else []

attrs = {
    "session.id":              session_id,
    "cwd":                     data.get("cwd", ""),
    "gen_ai.operation.name":   "post_tool_use",
    "gen_ai.tool.name":        tool_name,
    "gen_ai.tool.type":        "extension" if is_mcp else "function",
    "gen_ai.tool.success":     True,
    "tool_use_id":             tool_use_id,
    # duration_ms is provided directly by Claude Code (excludes permission wait)
    "tool.duration_ms":        data.get("duration_ms", 0),
}

if is_mcp and len(parts) >= 3:
    attrs["gen_ai.tool.mcp_server"] = parts[1]
    attrs["gen_ai.tool.mcp_action"] = parts[2]

if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(data.get("tool_input", {}))[:2000]

if os.environ.get("OTEL_LOG_TOOL_CONTENT") == "1":
    response = data.get("tool_response", "")
    attrs["gen_ai.tool.output"] = str(response)[:2000]

emit_span("claude_code.tool", attrs, start_time_ns=start_ns, end_time_ns=now)
