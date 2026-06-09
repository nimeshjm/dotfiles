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
import sys, os, time, json, datetime, re, glob, subprocess
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


def _extract_branch_ticket(cwd: str) -> str:
    """Return Jira ticket ID from the active git branch, e.g. 'CSMP-1234'."""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd or None,
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return ""
        m = re.search(r'\b([A-Z]+-\d+)\b', r.stdout.strip())
        return m.group(1) if m else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        _log(f"git branch error: {e}")
        return ""


def _read_turn_data(transcript_path: str) -> dict:
    """Extract tool calls, LOC changes, user prompt, and final summary for the current turn."""
    result: dict = {"user_prompt": "", "final_summary": "", "tool_calls": [], "loc_changes": {}}
    try:
        lines_raw: list = []
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            buf = b""
            while pos > 0 and len(lines_raw) < 300:
                chunk = min(8192, pos)
                pos -= chunk
                f.seek(pos)
                buf = f.read(chunk) + buf
                lines_raw = buf.split(b"\n")

        entries = []
        for raw in lines_raw:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

        tool_calls_rev = []
        final_summary = ""
        user_prompt = ""

        for entry in reversed(entries):
            etype = entry.get("type")
            msg = entry.get("message") or {}
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue

            if etype == "assistant":
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and not final_summary:
                        final_summary = block.get("text", "")
                    elif btype == "tool_use":
                        tool_calls_rev.append({
                            "name": block.get("name", ""),
                            "input": block.get("input") or {},
                        })
            elif etype == "user":
                text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if text_blocks:
                    user_prompt = text_blocks[0].get("text", "")
                    break

        tool_calls = list(reversed(tool_calls_rev))

        loc_changes: dict = {}
        for tc in tool_calls:
            name = tc["name"]
            inp = tc["input"]
            if name == "Edit":
                path = inp.get("file_path", "unknown")
                removed = len(inp.get("old_string", "").splitlines())
                added = len(inp.get("new_string", "").splitlines())
                if path not in loc_changes:
                    loc_changes[path] = {"added": 0, "removed": 0}
                loc_changes[path]["added"] += added
                loc_changes[path]["removed"] += removed
            elif name == "Write":
                path = inp.get("file_path", "unknown")
                added = len(inp.get("content", "").splitlines())
                if path not in loc_changes:
                    loc_changes[path] = {"added": 0, "removed": 0}
                loc_changes[path]["added"] += added

        result["user_prompt"] = user_prompt
        result["final_summary"] = final_summary
        result["tool_calls"] = tool_calls
        result["loc_changes"] = loc_changes

    except (OSError, ValueError) as e:
        _log(f"turn data read error: {e}")
    return result


def _find_recent_plan(cwd: str = "", tool_calls: list = None) -> str:
    """Return content of the plan file for this session.

    Priority:
    1. A plan file written/edited by a tool call in the current turn.
    2. A plan file whose name starts with the cwd-derived slug prefix (written within 7 days).
    3. The most recently modified plan within 24h (mtime fallback).
    """
    plans_dir = os.path.expanduser("~/.claude/plans")

    # 1. Tool-call evidence: most reliable — this turn explicitly wrote the plan
    if tool_calls:
        for tc in tool_calls:
            if tc["name"] in ("Write", "Edit"):
                path = tc["input"].get("file_path", "")
                if path.startswith(plans_dir) and path.endswith(".md"):
                    try:
                        with open(path) as f:
                            return f.read()
                    except OSError:
                        pass

    # 2. cwd slug prefix match — covers turns that read a plan created earlier this session
    if cwd:
        cwd_slug = cwd.lower().replace("/", "-").lstrip("-")
        prefix = cwd_slug[:35]
        try:
            candidates = [
                f for f in glob.glob(os.path.join(plans_dir, "*.md"))
                if os.path.basename(f).startswith(prefix)
            ]
            if candidates:
                candidates.sort(key=os.path.getmtime, reverse=True)
                if time.time() - os.path.getmtime(candidates[0]) < 86400 * 7:
                    with open(candidates[0]) as f:
                        return f.read()
        except OSError:
            pass

    # 3. Mtime fallback — last resort; may return a plan from a different session
    try:
        files = glob.glob(os.path.join(plans_dir, "*.md"))
        if not files:
            return ""
        files.sort(key=os.path.getmtime, reverse=True)
        if time.time() - os.path.getmtime(files[0]) > 86400:
            return ""
        with open(files[0]) as f:
            return f.read()
    except OSError:
        return ""


def _format_jira_comment(
    user_prompt: str,
    final_summary: str,
    tool_calls: list,
    loc_changes: dict,
    plan_content: str,
) -> str:
    """Build a Markdown comment summarising the turn."""
    parts = ["## Claude Code Turn Summary\n"]

    if user_prompt:
        truncated = user_prompt[:500] + ("..." if len(user_prompt) > 500 else "")
        parts.append(f"**Request:** {truncated}\n")

    if final_summary:
        parts.append("### Summary\n")
        truncated = final_summary[:2000] + ("..." if len(final_summary) > 2000 else "")
        parts.append(f"{truncated}\n")

    if tool_calls:
        parts.append("### Tools Called\n")
        counts: dict = {}
        for tc in tool_calls:
            counts[tc["name"]] = counts.get(tc["name"], 0) + 1
        for name, n in sorted(counts.items(), key=lambda x: -x[1]):
            parts.append(f"- {name} ({n}x)")
        parts.append("")

    if loc_changes:
        parts.append("### Code Changes\n")
        home = os.path.expanduser("~")
        for path, ch in loc_changes.items():
            short = path.replace(home, "~")
            parts.append(f"- `{short}`: +{ch['added']} / -{ch['removed']} lines")
        parts.append("")

    if plan_content:
        parts.append("---\n### Plan\n")
        truncated = plan_content[:3000] + ("..." if len(plan_content) > 3000 else "")
        parts.append(f"```\n{truncated}\n```")

    return "\n".join(parts)


def _post_jira_comment(ticket_id: str, comment: str) -> bool:
    """Post comment to a Jira ticket via the jira CLI."""
    try:
        r = subprocess.run(
            ["jira", "issue", "comment", "add", ticket_id, comment],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            _log(f"jira comment posted to {ticket_id}")
            return True
        _log(f"jira CLI error ({r.returncode}): {r.stderr[:200]}")
        return False
    except (OSError, subprocess.TimeoutExpired) as e:
        _log(f"jira comment error: {e}")
        return False


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

# Post a summary comment to the Jira ticket found in the current git branch
_cwd = data.get("cwd", "")
ticket_id = _extract_branch_ticket(_cwd)
if ticket_id:
    _log(f"posting jira comment to {ticket_id}")
    turn_data = _read_turn_data(transcript_path)
    plan_content = _find_recent_plan(cwd=_cwd, tool_calls=turn_data["tool_calls"])
    comment = _format_jira_comment(
        turn_data["user_prompt"],
        turn_data["final_summary"],
        turn_data["tool_calls"],
        turn_data["loc_changes"],
        plan_content,
    )
    _post_jira_comment(ticket_id, comment)
else:
    _log("no jira ticket in branch, skipping comment")
