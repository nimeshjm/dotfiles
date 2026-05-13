#!/usr/bin/env python3
"""
hook_user_prompt_submit.py
Fires each time the user submits a prompt, before Claude processes it.
Records prompt metadata (NOT the content — toggle OTEL_LOG_USER_PROMPTS to 1 to include it).

stdin fields:
  session_id, cwd, hook_event_name
  prompt: the full prompt text
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from otel_span import read_stdin, emit_span

data   = read_stdin()
now    = time.time_ns()
prompt = data.get("prompt", "")

attrs = {
    "session.id":              data.get("session_id", ""),
    "cwd":                     data.get("cwd", ""),
    "gen_ai.operation.name":   "user_prompt_submit",
    "prompt.char_length":      len(prompt),
    "prompt.word_count":       len(prompt.split()),
}

# Opt-in: include actual prompt text only when env var is set
if os.environ.get("OTEL_LOG_USER_PROMPTS") == "1":
    attrs["gen_ai.prompt"] = prompt[:2000]  # cap at 2 KB

emit_span(
    "claude_code.user_prompt",
    attrs,
    start_time_ns=now,
    end_time_ns=now,
)
