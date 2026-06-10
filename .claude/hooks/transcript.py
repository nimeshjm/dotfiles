#!/usr/bin/env python3
"""
transcript.py — read Claude Code transcript JSONL files.

The Stop hook payload omits model/usage/stop_reason and turn details, so
hook_stop.py reads them from the transcript instead. Transcripts can be
large, so everything here scans backwards from the end of the file.
"""
from __future__ import annotations

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
