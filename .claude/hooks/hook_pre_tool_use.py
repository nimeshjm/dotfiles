#!/usr/bin/env python3
"""
hook_pre_tool_use.py
Fires before every tool call. Records tool name, type, and (optionally) input.
Writes a start-time file so hook_post_tool_use.py can calculate duration.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input (dict — contents vary by tool)
"""
import sys, os, time, json, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data       = read_stdin()
now        = time.time_ns()
tool_name  = data.get("tool_name", "unknown")
tool_use_id= data.get("tool_use_id", tool_name)
tool_input = data.get("tool_input", {})
session_id = data.get("session_id", "")

# Persist start time so PostToolUse can compute duration
start_file = os.path.join(tempfile.gettempdir(), f"claude_hook_{session_id}_{tool_use_id}.start")
with open(start_file, "w") as f:
    f.write(str(now))

# Detect MCP vs built-in
is_mcp = tool_name.startswith("mcp__")
parts  = tool_name.split("__") if is_mcp else []

attrs = {
    "session.id":              session_id,
    "cwd":                     data.get("cwd", ""),
    "gen_ai.operation.name":   "pre_tool_use",
    "gen_ai.tool.name":        tool_name,
    "gen_ai.tool.type":        "extension" if is_mcp else "function",
    "tool_use_id":             tool_use_id,
}

if is_mcp and len(parts) >= 3:
    attrs["gen_ai.tool.mcp_server"] = parts[1]
    attrs["gen_ai.tool.mcp_action"] = parts[2]

# Opt-in: record tool input arguments
if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(tool_input)[:2000]

emit_span("claude_code.tool.pre", attrs, start_time_ns=now, end_time_ns=now)
