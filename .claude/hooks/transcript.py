#!/usr/bin/env python3
"""
transcript.py — read Claude Code transcript JSONL files.

The Stop hook payload omits model/usage/stop_reason and turn details, so
hook_stop.py reads them from the transcript instead. Transcripts can be
large, so everything here scans backwards from the end of the file.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Iterator

from otel_span import log_debug

_CHUNK_SIZE = 8192


def _iter_lines_reversed(path: str, max_lines: int | None = None) -> Iterator[bytes]:
    """Yield raw lines newest-first, reading the file backwards in chunks.

    Only complete lines are yielded: the first segment of each chunk may be
    a partial line, so it is held back until the preceding chunk arrives.
    """
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buf = b""
        yielded = 0
        while pos > 0:
            chunk = min(_CHUNK_SIZE, pos)
            pos -= chunk
            f.seek(pos)
            buf = f.read(chunk) + buf
            lines = buf.split(b"\n")
            buf = lines[0]  # possibly partial — completed by the next chunk
            for line in reversed(lines[1:]):
                yield line
                yielded += 1
                if max_lines is not None and yielded >= max_lines:
                    return
        yield buf


def iter_entries_reversed(path: str, max_lines: int | None = None) -> Iterator[dict]:
    """Yield parsed JSONL entries newest-first; blank/corrupt lines are skipped."""
    for raw in _iter_lines_reversed(path, max_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def last_assistant_message(path: str) -> dict[str, Any]:
    """Return model/stop_reason/usage from the newest assistant entry."""
    try:
        for entry in iter_entries_reversed(path):
            if entry.get("type") == "assistant":
                msg = entry.get("message", {}) or {}
                return {
                    "model":       msg.get("model", ""),
                    "stop_reason": msg.get("stop_reason", "unknown"),
                    "usage":       msg.get("usage", {}),
                }
    except (OSError, ValueError) as e:
        log_debug(f"transcript read error: {e}")
    return {"model": "", "stop_reason": "unknown", "usage": {}}


def _parse_ts_ns(ts: str) -> "int | None":
    """ISO8601 transcript timestamp -> epoch ns, or None if unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except ValueError:
        return None


def turn_llm_calls(path: str, max_lines: int = 2000,
                   include_content: bool = False) -> "list[dict[str, Any]]":
    """One record per deduped assistant API response in the current turn,
    oldest-first. A single API response spans multiple consecutive JSONL lines
    (one per content block) sharing message.id — collapse them, keeping the
    latest timestamp as end.

    Turn boundary: walk backwards to the last *real* user text entry. User
    entries whose content is a list of tool_result blocks (no "text" block)
    do NOT terminate the walk; entries flagged isMeta are also skipped.

    When include_content is True, each call dict gains:
    - content_blocks: assistant content blocks accumulated across all lines
      sharing the message.id
    - input_messages: list of {"role": "user" | "tool", "blocks": [...]}
      for each non-isMeta user entry since the previous API response

    Returns [] on any error (fail-open).
    """
    try:
        # 1. Collect this turn's entries, newest-first.
        entries: "list[dict]" = []
        for entry in iter_entries_reversed(path, max_lines):
            entries.append(entry)
            if entry.get("type") != "user" or entry.get("isMeta"):
                continue
            content = (entry.get("message") or {}).get("content")
            is_text_user = (
                (isinstance(content, str) and content.strip()) or
                (isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "text" for b in content))
            )
            if is_text_user:
                break  # boundary entry stays in the list: it seeds prev_ts

        # 2. Replay oldest-first, deduping assistant entries by message.id.
        entries.reverse()
        calls: "list[dict[str, Any]]" = []
        by_id: "dict[str, dict[str, Any]]" = {}
        prev_ts: "int | None" = None
        pending_input: "list[dict]" = []
        for entry in entries:
            ts = _parse_ts_ns(entry.get("timestamp", ""))
            if entry.get("type") == "assistant":
                msg = entry.get("message") or {}
                mid = msg.get("id", "")
                raw = msg.get("content")
                blocks = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
                if mid and mid in by_id:
                    if ts:                      # later line of same API response
                        by_id[mid]["end_ns"] = ts
                    if include_content:
                        by_id[mid]["content_blocks"].extend(blocks)
                elif mid:
                    call = {
                        "message_id":  mid,
                        "model":       msg.get("model", ""),
                        "stop_reason": msg.get("stop_reason") or "unknown",
                        "usage":       msg.get("usage") or {},
                        "start_ns":    prev_ts,   # may be None for the first call
                        "end_ns":      ts,
                    }
                    if include_content:
                        call["content_blocks"] = blocks
                        call["input_messages"] = pending_input
                        pending_input = []
                    by_id[mid] = call
                    calls.append(call)
            elif include_content and entry.get("type") == "user" and not entry.get("isMeta"):
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str) and content.strip():
                    pending_input.append({"role": "user", "blocks": [{"type": "text", "text": content}]})
                elif isinstance(content, list):
                    blocks = [b for b in content if isinstance(b, dict)]
                    if blocks:
                        pending_input.append({"role": "user", "blocks": blocks})
            if ts:
                prev_ts = ts
        return calls
    except (OSError, ValueError):
        return []


_GENAI_ATTR_CAP = 16_000  # chars per serialized gen_ai.*.messages attribute


def _flatten_tool_result(content: Any) -> str:
    """tool_result inner content -> plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, dict):
                parts.append(f"[{b.get('type', 'block')}]")
        return "\n".join(parts)
    return str(content)


def _block_to_part(block: "dict[str, Any]") -> "dict[str, Any] | None":
    """Transcript content block -> OTel GenAI semconv message part. None = skip."""
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "content": block.get("text", "")}
    if btype == "tool_use":
        return {"type": "tool_call", "id": block.get("id", ""),
                "name": block.get("name", ""), "arguments": block.get("input") or {}}
    if btype == "tool_result":
        return {"type": "tool_call_response", "id": block.get("tool_use_id", ""),
                "result": _flatten_tool_result(block.get("content"))}
    if btype == "thinking":
        return None  # large and has no stable semconv part type
    return {"type": "text", "content": f"[{btype} omitted]"}


def _dump_capped(messages: "list[dict]", cap: int) -> str:
    """json.dumps(messages) kept under cap by clamping part payloads, then
    dropping oldest messages. Always returns valid JSON (never a sliced string)."""
    def dump() -> str:
        return json.dumps(messages, ensure_ascii=False, default=str)

    s = dump()
    if len(s) <= cap:
        return s
    for limit in (4000, 1000, 200):
        for m in messages:
            for p in m.get("parts", []):
                for key in ("content", "result"):
                    v = p.get(key)
                    if isinstance(v, str) and len(v) > limit:
                        p[key] = v[:limit] + f"...[truncated {len(v) - limit} chars]"
                a = p.get("arguments")
                if a is not None:
                    aj = json.dumps(a, ensure_ascii=False, default=str)
                    if len(aj) > limit:
                        p["arguments"] = aj[:limit] + "...[truncated]"
        s = dump()
        if len(s) <= cap:
            return s
    while len(messages) > 1:
        messages.pop(0)
        s = dump()
        if len(s) <= cap:
            return s
    return json.dumps([{"role": "truncated", "parts": [
        {"type": "text", "content": "messages exceeded attribute cap"}]}])


def format_genai_input_messages(input_messages: "list[dict]",
                                cap: int = _GENAI_ATTR_CAP) -> str:
    """input_messages records from turn_llm_calls -> gen_ai.input.messages JSON.
    Role is "tool" when an entry is purely tool_result blocks (semconv style)."""
    try:
        messages = []
        for im in input_messages:
            blocks = im.get("blocks") or []
            parts = [p for p in (_block_to_part(b) for b in blocks) if p]
            if not parts:
                continue
            all_tool_results = all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
            messages.append({"role": "tool" if all_tool_results else "user",
                             "parts": parts})
        return _dump_capped(messages, cap)
    except Exception:
        return "[]"


def format_genai_output_messages(content_blocks: "list[dict]", stop_reason: str,
                                 cap: int = _GENAI_ATTR_CAP) -> str:
    """Assistant content blocks for one API response -> gen_ai.output.messages JSON."""
    try:
        parts = [p for p in (_block_to_part(b) for b in content_blocks) if p]
        return _dump_capped(
            [{"role": "assistant", "parts": parts, "finish_reason": stop_reason}], cap)
    except Exception:
        return "[]"


def read_turn_data(path: str, max_lines: int = 300) -> dict[str, Any]:
    """Extract tool calls, LOC changes, user prompt, and final summary for the
    current turn (entries newest-first back to the last user text message)."""
    result: dict[str, Any] = {
        "user_prompt": "", "final_summary": "", "tool_calls": [], "loc_changes": {},
    }
    try:
        tool_calls_rev: list[dict] = []
        final_summary = ""
        user_prompt = ""

        for entry in iter_entries_reversed(path, max_lines):
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

        # Tally lines added/removed per file from Edit/Write inputs
        loc_changes: dict[str, dict[str, int]] = {}
        for tc in tool_calls:
            name, inp = tc["name"], tc["input"]
            if name not in ("Edit", "Write"):
                continue
            path_key = inp.get("file_path", "unknown")
            change = loc_changes.setdefault(path_key, {"added": 0, "removed": 0})
            if name == "Edit":
                change["removed"] += len(inp.get("old_string", "").splitlines())
                change["added"]   += len(inp.get("new_string", "").splitlines())
            else:
                change["added"]   += len(inp.get("content", "").splitlines())

        result.update(
            user_prompt=user_prompt,
            final_summary=final_summary,
            tool_calls=tool_calls,
            loc_changes=loc_changes,
        )
    except (OSError, ValueError) as e:
        log_debug(f"turn data read error: {e}")
    return result
