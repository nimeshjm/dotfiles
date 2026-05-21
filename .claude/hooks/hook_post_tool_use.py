#!/usr/bin/env python3
"""
hook_post_tool_use.py
Fires after a tool call succeeds. Emits a span covering the full tool duration
by reading the start timestamp written by hook_pre_tool_use.py.

stdin fields:
  session_id, cwd, hook_event_name
  tool_name, tool_use_id, tool_input, tool_response, duration_ms
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

data        = read_stdin()
now         = time.time_ns()
tool_name   = data.get("tool_name", "unknown")
tool_use_id = data.get("tool_use_id", tool_name)
session_id  = data.get("session_id", "")

# Retrieve the start time written by PreToolUse
start_ns   = now
start_file = _state_path(f"claude_hook_{session_id}_{tool_use_id}.start")
if os.path.exists(start_file):
    try:
        with open(start_file) as f:
            start_ns = int(f.read().strip())
        os.unlink(start_file)
    except (ValueError, OSError):
        pass

# Read turn_id if available (written by UserPromptSubmit, cleared by Stop)
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
    "gen_ai.tool.success":   True,
    "tool_use_id":           tool_use_id,
1    "tool.duration_ms":      (now - start_ns) // 1_000_000,
}

if is_mcp and len(parts) >= 3:
    attrs["gen_ai.tool.mcp_server"] = parts[1]
    attrs["gen_ai.tool.mcp_action"] = parts[2]

# Lines changed for Edit/Write — proxy for code output volume
tool_input_data = data.get("tool_input", {}) or {}
if tool_name == "Edit":
    old = tool_input_data.get("old_string", "")
    new = tool_input_data.get("new_string", "")
    attrs["edit.lines_removed"] = len(old.splitlines())
    attrs["edit.lines_added"]   = len(new.splitlines())
elif tool_name == "Write":
    content = tool_input_data.get("content", "")
    attrs["write.lines"] = len(content.splitlines())

if os.environ.get("OTEL_LOG_TOOL_DETAILS") == "1":
    attrs["gen_ai.tool.input"] = json.dumps(tool_input_data)[:2000]

if os.environ.get("OTEL_LOG_TOOL_CONTENT") == "1":
    response = data.get("tool_response", "")
    attrs["gen_ai.tool.output"] = str(response)[:2000]

emit_span("claude_code.tool", attrs, start_time_ns=start_ns, end_time_ns=now)
