#!/usr/bin/env python3
"""
hook_user_prompt_submit.py
Fires each time the user submits a prompt, before Claude processes it.
Generates a turn_id that flows through all tool and stop spans for this turn.

stdin fields:
  session_id, cwd, hook_event_name
  prompt: the full prompt text
"""
import sys, os, time, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _open_state_file

data       = read_stdin()
now        = time.time_ns()
prompt     = data.get("prompt", "")
session_id = data.get("session_id", "")

# Generate a turn_id and persist it — PreToolUse/PostToolUse/Stop read it
turn_id = str(uuid.uuid4())
try:
    with _open_state_file(f"claude_turn_{session_id}.id") as f:
        f.write(turn_id)
except OSError:
    pass

attrs = {
    "session.id":         session_id,
    "cwd":                data.get("cwd", ""),
    "turn.id":            turn_id,
    "prompt.char_length": len(prompt),
    "prompt.word_count":  len(prompt.split()),
}

# Tag slash commands so skill/command usage is queryable
stripped = prompt.lstrip()
if stripped.startswith("/"):
    attrs["command.name"] = stripped.split()[0] if stripped.split() else "/"

if os.environ.get("OTEL_LOG_USER_PROMPTS") == "1":
    attrs["gen_ai.prompt"] = prompt[:2000]

emit_span(
    "claude_code.user_prompt",
    attrs,
    start_time_ns=now,
    end_time_ns=now,
)
