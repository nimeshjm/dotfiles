#!/usr/bin/env python3
"""
hook_post_tool_use_failure.py
Fires after a tool call fails. Emits an error span.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input, error, duration_ms
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")
error       = str(data.get("error", "unknown error"))

start_ns = now
start_file = _state_path(f"claude_hook_{session_id}_{tool_use_id}.start")
if os.path.exists(start_file):
    try:
        with open(start_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(start_file)
    except (ValueError, OSError):
        pass

# Read turn_id if available
turn_id   = ""
turn_file = _state_path(f"claude_turn_{session_id}.id")
if os.path.exists(turn_file):
    try:
        with open(turn_file) as f:
            turn_id = f.read().strip()
    except OSError:
        pass

is_mcp = tool_name.startswith("mcp__")
parts  = tool_name.split("__") if is_mcp else []

attrs = {
    "session.id":            session_id,
    "cwd":                   data.get("cwd", ""),
    "turn.id":               turn_id,
    "gen_ai.operation.name": "tool_call",
    "gen_ai.tool.name":      tool_name,
    "gen_ai.tool.type":      "extension" if is_mcp else "function",
    "gen_ai.tool.success":   False,
    "tool_use_id":           tool_use_id,
    "tool.duration_ms":      data.get("duration_ms", 0),
    "error.message":         error[:500],
    "error.type":            data.get("error_type", "unknown"),
}

if is_mcp and len(parts) >= 3:
    attrs["gen_ai.tool.mcp_server"] = parts[1]
    attrs["gen_ai.tool.mcp_action"] = parts[2]

if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(data.get("tool_input", {}))[:2000]

emit_span(
    "claude_code.tool",
    attrs,
    start_time_ns=start_ns,
    end_time_ns=now,
    status_ok=False,
    error_message=error,
)
