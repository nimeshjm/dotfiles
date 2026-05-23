#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
HOME_CLAUDE = Path.home() / ".claude"

# ---------------------------------------------------------------------------
# Settings merge
# ---------------------------------------------------------------------------

def _merge_hook_array(dest_arr, dotfile_arr):
    index = {e.get("matcher"): e for e in dest_arr}
    index.update({e.get("matcher"): e for e in dotfile_arr})
    named = sorted((k, v) for k, v in index.items() if k is not None)
    catchall = [(k, v) for k, v in index.items() if k is None]
    return [v for _, v in named + catchall]


def _deep_merge(dest, src):
    result = dict(dest)
    for key, val in src.items():
        if key not in result:
            result[key] = val
        elif key == "hooks" and isinstance(result[key], dict) and isinstance(val, dict):
            merged = dict(result[key])
            for event, arr in val.items():
                if event not in merged:
                    merged[event] = arr
                elif isinstance(merged[event], list) and isinstance(arr, list):
                    merged[event] = _merge_hook_array(merged[event], arr)
                else:
                    merged[event] = arr
            result[key] = merged
        elif isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def install_hooks():
    shutil.copytree(SCRIPT_DIR / "hooks", HOME_CLAUDE / "hooks", dirs_exist_ok=True)


def merge_settings():
    dotfile = SCRIPT_DIR / "settings.json"
    dest = HOME_CLAUDE / "settings.json"
    if not dotfile.exists():
        return
    src = json.loads(dotfile.read_text())
    merged = _deep_merge(json.loads(dest.read_text()), src) if dest.exists() else src
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dest)
    except Exception:
        os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Honeycomb board definition
#
# This is the canonical panel spec for the "Claude Code Sessions" board.
# To create the board, open Claude Code in this repo and prompt:
#   "Create a Honeycomb board called 'Claude Code Sessions' based on
#    the PANELS definition in install.py"
# Claude will read this file, run each query via the Honeycomb MCP, and
# create the board — no management key required.
#
# Time-series panels use bar charts for better discrete-time visualization.
# Table panels show category breakdowns with counts and aggregates.
# ---------------------------------------------------------------------------

PANELS = [
    # Row 0 (y=0, h=6): Activity counts
    {"name": "Sessions Started",             "desc": "Count of new sessions",
     "chart_type": "bar", "x": 0,  "y": 0,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.session.start"}],
              "time_range": 86400}},
    {"name": "Tool Calls",                   "desc": "Total tool invocations",
     "chart_type": "bar", "x": 4,  "y": 0,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.tool"}],
              "time_range": 86400}},
    {"name": "User Prompts",                 "desc": "Total prompts submitted",
     "chart_type": "bar", "x": 8,  "y": 0,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.user_prompt"}],
              "time_range": 86400}},
    # Row 6 (y=6, h=6): Session health + model usage
    {"name": "Session Duration (avg + p95)", "desc": "How long sessions last in ms",
     "chart_type": "bar", "x": 0,  "y": 6,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "AVG", "column": "session.duration_ms"},
                                {"op": "P95", "column": "session.duration_ms"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.session.end"}],
              "time_range": 86400}},
    {"name": "Cache Hit Ratio",              "desc": "Average prompt-cache hit rate per turn",
     "chart_type": "bar", "x": 4,  "y": 6,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "AVG", "column": "gen_ai.usage.cache_hit_ratio"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.turn.stop"}],
              "time_range": 86400}},
    {"name": "Model Usage",                  "desc": "Turns by model over time",
     "chart_type": "bar", "x": 8,  "y": 6,  "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.turn.stop"}],
              "breakdowns": ["gen_ai.request.model"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 10, "time_range": 86400}},
    # Row 12 (y=12, h=6): Token usage trend (full width)
    {"name": "Token Usage",                  "desc": "Input, cache-read, and output tokens over time",
     "chart_type": "bar", "x": 0,  "y": 12, "w": 12, "h": 6,
     "spec": {"calculations": [{"op": "SUM", "column": "gen_ai.usage.input_tokens"},
                                {"op": "SUM", "column": "gen_ai.usage.cache_read_tokens"},
                                {"op": "SUM", "column": "gen_ai.usage.output_tokens"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.turn.stop"}],
              "time_range": 86400}},
    # Row 18 (y=18, h=5): Tool failures and permission denials (side by side)
    {"name": "Tool Failures",                "desc": "Failed tool call count by tool name",
     "style": "table", "x": 0,  "y": 18, "w": 6,  "h": 5,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.tool"},
                          {"column": "gen_ai.tool.success", "op": "=", "value": False}],
              "breakdowns": ["gen_ai.tool.name"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 20, "time_range": 86400}},
    # Row 26 (y=26, h=8): Tool perf tables
    {"name": "Tool Duration (avg + p95)",    "desc": "Average and p95 tool execution time by tool type",
     "style": "table", "x": 0,  "y": 26, "w": 6,  "h": 8,
     "spec": {"calculations": [{"op": "AVG", "column": "tool.duration_ms"},
                                {"op": "P95", "column": "tool.duration_ms"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.tool"}],
              "breakdowns": ["gen_ai.tool.name"],
              "orders": [{"op": "AVG", "column": "tool.duration_ms", "order": "descending"}],
              "limit": 20, "time_range": 86400}},
    {"name": "Lines Edited per Session",     "desc": "Lines added and removed per session",
     "style": "table", "x": 6,  "y": 26, "w": 6,  "h": 8,
     "spec": {"calculations": [{"op": "SUM", "column": "edit.lines_added"},
                                {"op": "SUM", "column": "edit.lines_removed"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.tool"},
                          {"column": "gen_ai.tool.name", "op": "=", "value": "Edit"}],
              "breakdowns": ["session.id"],
              "orders": [{"op": "SUM", "column": "edit.lines_added", "order": "descending"}],
              "limit": 50, "time_range": 86400}},
    # Row 34 (y=34, h=8): Per-session depth + token cost
    {"name": "Prompts per Session",          "desc": "Depth of each session by prompt count",
     "style": "table", "x": 0,  "y": 34, "w": 6,  "h": 8,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.user_prompt"}],
              "breakdowns": ["session.id"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 50, "time_range": 86400}},
    {"name": "Tokens per Session",           "desc": "Token consumption breakdown by session",
     "style": "table", "x": 6,  "y": 34, "w": 6,  "h": 8,
     "spec": {"calculations": [{"op": "SUM", "column": "gen_ai.usage.input_tokens"},
                                {"op": "SUM", "column": "gen_ai.usage.cache_read_tokens"},
                                {"op": "SUM", "column": "gen_ai.usage.output_tokens"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.turn.stop"}],
              "breakdowns": ["session.id"],
              "orders": [{"op": "SUM", "column": "gen_ai.usage.input_tokens", "order": "descending"}],
              "limit": 50, "time_range": 86400}},
    # Row 42 (y=42, h=6): Stop reason + subagent + compaction
    {"name": "Stop Reason Distribution",     "desc": "How turns end: end_turn vs max_tokens vs other",
     "style": "table", "x": 0,  "y": 42, "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.turn.stop"}],
              "breakdowns": ["agent.stop_reason"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 10, "time_range": 86400}},
    {"name": "Subagent Activity",            "desc": "Subagent invocations and avg duration by type",
     "style": "table", "x": 4,  "y": 42, "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}, {"op": "AVG", "column": "agent.duration_ms"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.subagent"}],
              "breakdowns": ["agent.type"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 20, "time_range": 86400}},
    {"name": "Context Compaction",           "desc": "Compaction events and tokens saved",
     "style": "table", "x": 8,  "y": 42, "w": 4,  "h": 6,
     "spec": {"calculations": [{"op": "COUNT"}, {"op": "SUM", "column": "context.tokens_saved"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.context.compact"}],
              "time_range": 86400}},
    {"name": "Permission Denials",           "desc": "Auto-mode permission denials by tool",
     "style": "table", "x": 6,  "y": 18, "w": 6,  "h": 5,
     "spec": {"calculations": [{"op": "COUNT"}],
              "filters": [{"column": "name", "op": "=", "value": "claude_code.permission.denied"}],
              "breakdowns": ["gen_ai.tool.name"],
              "orders": [{"op": "COUNT", "order": "descending"}],
              "limit": 20, "time_range": 86400}},
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    install_hooks()
    merge_settings()
    print("Hooks and settings installed.")
    print()
    print("To create the Honeycomb dashboard, open Claude Code in this repo and prompt:")
    print('  "Create a Honeycomb board called \'Claude Code Sessions\' based on')
    print('   the PANELS definition in install.py"')
    print()
    print("Board features:")
    print("  • Time-series panels use bar charts for discrete-time visualization")
    print("  • Activity metrics: sessions, tool calls, user prompts")
    print("  • Performance: latency, cache hit ratio, token usage")
    print("  • Category breakdowns: by model, tool, session, stop reason")
    print()
    print("Requires the Honeycomb MCP server configured in Claude Code.")


if __name__ == "__main__":
    main()
