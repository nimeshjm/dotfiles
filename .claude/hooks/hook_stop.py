#!/usr/bin/env python3
"""
hook_stop.py
Fires each time Claude finishes responding (once per turn).
Records stop reason, token usage, model, and turn linkage.

stdin fields (Claude Code Stop hook payload — does NOT include model/usage/stop_reason):
  session_id, cwd, hook_event_name, transcript_path

model, stop_reason, and usage are read from the last assistant entry in the
transcript JSONL (transcript_path), since the Stop payload omits them.
"""
import sys, os, time, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otel_span import read_stdin, emit_span, _state_path

_DEBUG_DIR = os.path.expanduser("~/.cache/claude-hooks")
_LOG_FILE  = os.path.join(_DEBUG_DIR, "hook_stop.log")

def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {msg}\n"
    print(f"[hook_stop] {msg}", file=sys.stderr)
    try:
        os.makedirs(_DEBUG_DIR, mode=0o700, exist_ok=True)
        with open(_LOG_FILE, "a") as _lf:
            _lf.write(line)
    except OSError:
        pass

data        = read_stdin()
now         = time.time_ns()
session_id  = data.get("session_id", "")
_log(f"invoked session={session_id} endpoint={os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT','NOT_SET')}")

# Capture raw payload for debugging: cat ~/.cache/claude-hooks/last_stop_payload.json
try:
    _debug_dir = os.path.expanduser("~/.cache/claude-hooks")
    os.makedirs(_debug_dir, mode=0o700, exist_ok=True)
    with open(os.path.join(_debug_dir, "last_stop_payload.json"), "w") as _f:
        json.dump(data, _f, indent=2)
except OSError:
    pass


def _read_last_assistant(transcript_path: str) -> dict:
    """Return model/stop_reason/usage from the last assistant entry in the transcript."""
    try:
        with open(transcript_path, "rb") as f:
            # Scan backwards through the file to avoid loading large transcripts
            f.seek(0, 2)
            pos = f.tell()
            buf = b""
            while pos > 0:
                chunk = min(4096, pos)
                pos -= chunk
                f.seek(pos)
                buf = f.read(chunk) + buf
                for line in reversed(buf.split(b"\n")):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "assistant":
                        msg = entry.get("message", {}) or {}
                        return {
                            "model":       msg.get("model", ""),
                            "stop_reason": msg.get("stop_reason", "unknown"),
                            "usage":       msg.get("usage", {}),
                        }
    except (OSError, ValueError) as e:
        _log(f"transcript read error: {e}")
    return {"model": "", "stop_reason": "unknown", "usage": {}}


transcript_path = data.get("transcript_path", "")
_log(f"reading transcript path={transcript_path!r} exists={os.path.exists(transcript_path)}")
transcript_data = _read_last_assistant(transcript_path)
usage       = transcript_data["usage"] or {}
stop_reason = transcript_data["stop_reason"]
model       = transcript_data["model"] or os.environ.get("ANTHROPIC_MODEL", "")
_log(f"transcript result: model={model!r} stop_reason={stop_reason!r} input_tokens={usage.get('input_tokens', 0)}")

# Read and clear the turn_id written by UserPromptSubmit
turn_id   = ""
turn_file = _state_path(f"claude_turn_{session_id}.id")
if os.path.exists(turn_file):
    try:
        with open(turn_file) as f:
            turn_id = f.read().strip()
        os.unlink(turn_file)
    except OSError:
        pass

# Pre-compute cache hit ratio so dashboards don't need derived columns
cache_read   = usage.get("cache_read_input_tokens", 0)
input_tokens = usage.get("input_tokens", 0)
cache_total  = cache_read + input_tokens
cache_hit_ratio = round(cache_read / cache_total, 4) if cache_total > 0 else 0.0

_log(f"calling emit_span session={session_id} turn={turn_id}")
emit_span(
    "claude_code.turn.stop",
    {
        "session.id":                         session_id,
        "cwd":                                data.get("cwd", ""),
        "turn.id":                            turn_id,
        "gen_ai.operation.name":              "chat",
        "gen_ai.request.model":               model,
        "agent.stop_reason":                  stop_reason,
        "gen_ai.usage.input_tokens":          input_tokens,
        "gen_ai.usage.output_tokens":         usage.get("output_tokens", 0),
        "gen_ai.usage.cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "gen_ai.usage.cache_read_tokens":     cache_read,
        "gen_ai.usage.cache_hit_ratio":       cache_hit_ratio,
    },
    start_time_ns=now,
    end_time_ns=now,
    status_ok=stop_reason not in ("error", "max_turns"),
    error_message=stop_reason if stop_reason in ("error", "max_turns") else "",
)
_log("emit_span complete")
