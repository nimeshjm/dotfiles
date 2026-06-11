#!/usr/bin/env python3
"""
install.py — Claude Code hooks installer with multi-backend OTel support.

Usage:
  python3 install.py                          # Honeycomb (default)
  python3 install.py --backend signoz         # SigNoz self-hosted (http://localhost:4318)
  python3 install.py --backend signoz \\
      --signoz-url http://localhost:3301 \\
      --signoz-api-key <key>                  # SigNoz + API POST

SigNoz mode writes ~/.claude/signoz_dashboard.json.  Import via:
  SigNoz UI → Dashboards → New Dashboard → Import JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import string
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
HOME_CLAUDE = Path.home() / ".claude"

# Aggregations are referenced by letter (A, B, C …) in formulas on both backends
QUERY_NAMES = string.ascii_uppercase

# ---------------------------------------------------------------------------
# Settings merge helpers (unchanged)
# ---------------------------------------------------------------------------


def _merge_hook_array(dest_arr: list, dotfile_arr: list) -> list:
    index = {e.get("matcher"): e for e in dest_arr}
    index.update({e.get("matcher"): e for e in dotfile_arr})
    named = sorted((k, v) for k, v in index.items() if k is not None)
    catchall = [(k, v) for k, v in index.items() if k is None]
    return [v for _, v in named + catchall]


def _deep_merge(dest: dict, src: dict) -> dict:
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


def install_hooks() -> None:
    shutil.copytree(
        SCRIPT_DIR / "hooks",
        HOME_CLAUDE / "hooks",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def merge_settings(backend: "Backend") -> None:
    """Merge .claude/settings.json into ~/.claude/settings.json.

    Rewrites OTEL endpoint/headers env vars in both the top-level env block
    and every inline hook command prefix, using the chosen backend's values.
    """
    dotfile = SCRIPT_DIR / "settings.json"
    dest = HOME_CLAUDE / "settings.json"
    if not dotfile.exists():
        return

    src: dict = json.loads(dotfile.read_text())
    env = backend.otel_env()

    # 1. Overwrite env block
    src.setdefault("env", {})
    src["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] = env["OTEL_EXPORTER_OTLP_ENDPOINT"]
    src["env"]["OTEL_EXPORTER_OTLP_HEADERS"] = env["OTEL_EXPORTER_OTLP_HEADERS"]

    # 2. Rewrite inline env var prefixes on every hook command.
    #    Values never contain spaces (we control the template), so \S* is safe.
    #    Use \S* (not \S+) to also match already-empty values.
    endpoint_token = f"OTEL_EXPORTER_OTLP_ENDPOINT={env['OTEL_EXPORTER_OTLP_ENDPOINT']}"
    headers_token = f"OTEL_EXPORTER_OTLP_HEADERS={env['OTEL_EXPORTER_OTLP_HEADERS']}"

    def _rewrite(cmd: str) -> str:
        cmd = re.sub(r"OTEL_EXPORTER_OTLP_ENDPOINT=\S*", endpoint_token, cmd)
        cmd = re.sub(r"OTEL_EXPORTER_OTLP_HEADERS=\S*", headers_token, cmd)
        return cmd

    for _event, event_hooks in src.get("hooks", {}).items():
        for hook_entry in event_hooks:
            for hook in hook_entry.get("hooks", []):
                if "command" in hook:
                    hook["command"] = _rewrite(hook["command"])

    # 3. Deep-merge into ~/.claude/settings.json
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
# Neutral panel intermediate representation (IR)
#
# Each panel has:
#   name       — display title
#   desc       — subtitle / tooltip
#   chart_type — "bar" | "table"   (normalised; no more "style": "table")
#   layout     — {x, y, w, h}      (grid units)
#   query:
#     span_name     — WHERE name = <span_name>
#     filters       — additional WHERE clauses [{field, op, value}, ...]
#     aggregations  — [{op, field?}, ...]  (field absent for COUNT)
#     breakdowns    — optional [field, ...]  (GROUP BY)
#     orders        — optional [{op, field?, order}, ...]  (ORDER BY)
#     limit         — optional int
#     time_range    — seconds (default 86400)
# ---------------------------------------------------------------------------

PANELS: list[dict[str, Any]] = [
    # ── Row 0 (y=0, h=6): Activity counts ──────────────────────────────────
    {
        "name": "Sessions Started",
        "desc": "Count of new sessions",
        "chart_type": "bar",
        "layout": {"x": 0, "y": 0, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.session.start",
            "aggregations": [{"op": "COUNT"}],
            "time_range": 86400,
        },
    },
    {
        "name": "Tool Calls",
        "desc": "Total tool invocations",
        "chart_type": "bar",
        "layout": {"x": 4, "y": 0, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.tool",
            "aggregations": [{"op": "COUNT"}],
            "time_range": 86400,
        },
    },
    {
        "name": "User Prompts",
        "desc": "Total prompts submitted",
        "chart_type": "bar",
        "layout": {"x": 8, "y": 0, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.user_prompt",
            "aggregations": [{"op": "COUNT"}],
            "time_range": 86400,
        },
    },
    # ── Row 6 (y=6, h=6): Session health + model usage ─────────────────────
    {
        "name": "Session Duration (avg + p95)",
        "desc": "How long sessions last in ms",
        "chart_type": "bar",
        "y_axis_unit": "ms",
        "layout": {"x": 0, "y": 6, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.session.end",
            "aggregations": [
                {"op": "AVG", "field": "session.duration_ms"},
                {"op": "P95", "field": "session.duration_ms"},
            ],
            "time_range": 86400,
        },
    },
    {
        "name": "Cache Hit Ratio",
        "desc": "Average prompt-cache hit rate per turn",
        "chart_type": "bar",
        "y_axis_unit": "percentunit",
        "layout": {"x": 4, "y": 6, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [{"op": "AVG", "field": "gen_ai.usage.cache_hit_ratio"}],
            "time_range": 86400,
        },
    },
    {
        "name": "Model Usage",
        "desc": "Turns by model over time",
        "chart_type": "bar",
        "layout": {"x": 8, "y": 6, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["gen_ai.request.model"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 10,
            "time_range": 86400,
        },
    },
    # ── Row 12 (y=12, h=6): Token usage (full width) ───────────────────────
    {
        "name": "Token Usage",
        "desc": (
            "Input, cache-read, and output tokens over time. "
            "Healthy pattern: cache_read_tokens dominates (typically 90%+ of total) — "
            "cached context is re-read cheaply without re-encoding. "
            "input_tokens are new uncached tokens Claude processes fresh; keep these low. "
            "output_tokens reflect response length and are typically 2–5% of total. "
            "If input_tokens ≈ cache_read_tokens, caching is underperforming — "
            "check for short sessions, frequent /clear, or context window resets."
        ),
        "chart_type": "bar",
        "y_axis_unit": "short",
        "decimal_precision": 4,
        "layout": {"x": 0, "y": 12, "w": 12, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},
            ],
            # B = cache_read, A = input → B/(A+B) = vol-weighted cache ratio
            "formulas": [{"expression": "B/(A+B)", "legend": "vol-weighted cache ratio"}],
            "time_range": 86400,
        },
    },
    # ── Row 18 (y=18, h=5): Tool failures + permission denials ─────────────
    {
        "name": "Tool Failures",
        "desc": "Failed tool call count by tool name",
        "chart_type": "table",
        "layout": {"x": 0, "y": 18, "w": 6, "h": 5},
        "query": {
            "span_name": "claude_code.tool",
            "filters": [{"field": "gen_ai.tool.success", "op": "=", "value": False}],
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["gen_ai.tool.name"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    {
        "name": "Permission Denials",
        "desc": "Auto-mode permission denials by tool",
        "chart_type": "table",
        "layout": {"x": 6, "y": 18, "w": 6, "h": 5},
        "query": {
            "span_name": "claude_code.permission.denied",
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["gen_ai.tool.name"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    # ── Row 26 (y=26, h=8): Tool perf tables ───────────────────────────────
    {
        "name": "Tool Duration (avg + p95)",
        "desc": "Average and p95 tool execution time by tool type",
        "chart_type": "table",
        "y_axis_unit": "ns",
        "layout": {"x": 0, "y": 26, "w": 6, "h": 8},
        "query": {
            "span_name": "claude_code.tool",
            "aggregations": [
                {"op": "AVG", "field": "duration_ms"},
                {"op": "P95", "field": "duration_ms"},
            ],
            "breakdowns": ["gen_ai.tool.name"],
            "orders": [{"op": "AVG", "field": "duration_ms", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    {
        "name": "Lines Edited per Session",
        "desc": "Lines added and removed per session",
        "chart_type": "table",
        "layout": {"x": 6, "y": 26, "w": 6, "h": 8},
        "query": {
            "span_name": "claude_code.tool",
            "filters": [{"field": "gen_ai.tool.name", "op": "=", "value": "Edit"}],
            "aggregations": [
                {"op": "SUM", "field": "edit.lines_added"},
                {"op": "SUM", "field": "edit.lines_removed"},
            ],
            "breakdowns": ["session.id"],
            "orders": [{"op": "SUM", "field": "edit.lines_added", "order": "descending"}],
            "limit": 50,
            "time_range": 86400,
        },
    },
    # ── Row 34 (y=34, h=8): Per-session depth + token cost ─────────────────
    {
        "name": "Prompts per Session",
        "desc": "Depth of each session by prompt count",
        "chart_type": "table",
        "layout": {"x": 0, "y": 34, "w": 6, "h": 8},
        "query": {
            "span_name": "claude_code.user_prompt",
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["session.id"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 50,
            "time_range": 86400,
        },
    },
    {
        "name": "Tokens per Session",
        "desc": (
            "Token consumption breakdown by session. "
            "Healthy: cache_read_tokens >> input_tokens — most context is re-read from cache "
            "rather than processed as new tokens. "
            "Cache hit ratio ≈ cache_read / (input + cache_read); aim for >80%. "
            "High input_tokens relative to cache_read signals short sessions or frequent resets. "
            "output_tokens reflect response verbosity; spikes here mean unusually long replies."
        ),
        "chart_type": "table",
        "decimal_precision": 4,
        "layout": {"x": 6, "y": 34, "w": 6, "h": 8},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},
            ],
            # B = cache_read, A = input → B/(A+B) = vol-weighted cache ratio per session
            "formulas": [{"expression": "B/(A+B)", "legend": "vol-weighted cache ratio"}],
            "breakdowns": ["session.id"],
            "orders": [{"op": "SUM", "field": "gen_ai.usage.input_tokens", "order": "descending"}],
            "limit": 50,
            "time_range": 86400,
        },
    },
    # ── Row 48 (y=48, h=6): API errors ─────────────────────────────────────
    {
        "name": "API Errors",
        "desc": "API errors grouped by error type and status code",
        "chart_type": "table",
        "layout": {"x": 0, "y": 48, "w": 12, "h": 6},
        "query": {
            "span_name": "api_error",
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["error", "status_code"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 50,
            "time_range": 86400,
        },
    },
    # ── Row 54 (y=54, h=6): Stop reason + subagent + compaction ────────────
    {
        "name": "Stop Reason Distribution",
        "desc": "How turns end: end_turn vs max_tokens vs other",
        "chart_type": "table",
        "layout": {"x": 0, "y": 54, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [{"op": "COUNT"}],
            "breakdowns": ["agent.stop_reason"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 10,
            "time_range": 86400,
        },
    },
    {
        "name": "Subagent Activity",
        "desc": "Subagent invocations and avg duration by type",
        "chart_type": "table",
        "layout": {"x": 4, "y": 54, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.subagent",
            "aggregations": [
                {"op": "COUNT"},
                {"op": "AVG", "field": "agent.duration_ms"},
            ],
            "breakdowns": ["agent.type"],
            "orders": [{"op": "COUNT", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    {
        "name": "Context Compaction",
        "desc": "Compaction events and tokens saved",
        "chart_type": "table",
        "layout": {"x": 8, "y": 54, "w": 4, "h": 6},
        "query": {
            "span_name": "claude_code.context.compact",
            "aggregations": [
                {"op": "COUNT"},
                {"op": "SUM", "field": "context.tokens_saved"},
            ],
            "time_range": 86400,
        },
    },
    # ── Row 60 (y=60, h=6): Tokens per model ───────────────────────────────
    {
        "name": "Tokens per Model",
        "desc": "Total input, cache-read, output, and cache-creation tokens broken down by model",
        "chart_type": "table",
        "y_axis_unit": "short",
        "layout": {"x": 0, "y": 60, "w": 12, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_creation_tokens"},
            ],
            "breakdowns": ["gen_ai.request.model"],
            "orders": [{"op": "SUM", "field": "gen_ai.usage.input_tokens", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    # ── Row 66 (y=66, h=6): Estimated cost per model ────────────────────────
    {
        "name": "Estimated Cost per Model (USD)",
        "desc": (
            "Approximate USD cost per model per token type. "
            "Rates (verify at anthropic.com/pricing): "
            "Sonnet $3/$15/$0.30/$3.75 per MTok input/output/cache-read/cache-creation; "
            "Haiku $0.80/$4/$0.08/$1. "
            "Formula uses Sonnet rates for all models — Haiku cost is overestimated ~3–4x. "
            "New model slugs get cost=0 until rates are added."
        ),
        "chart_type": "table",
        "decimal_precision": 4,
        "layout": {"x": 0, "y": 66, "w": 12, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},        # A
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},       # B
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},   # C
                {"op": "SUM", "field": "gen_ai.usage.cache_creation_tokens"},  # D
            ],
            # Sonnet-rate formula: $3/$15/$0.30/$3.75 per MTok
            "formulas": [
                {
                    "expression": "A * 0.000003 + B * 0.000015 + C * 0.0000003 + D * 0.00000375",
                    "legend": "total_cost_usd",
                }
            ],
            "breakdowns": ["gen_ai.request.model"],
            "limit": 20,
            "time_range": 86400,
        },
    },
    # ── Row 72 (y=72, h=6): Tokens per repo ─────────────────────────────────
    {
        "name": "Tokens per Repo",
        "desc": (
            "Total input, cache-read, output, and cache-creation tokens broken down "
            "by repo (git.repo; blank = non-git cwd or HTTPS remote)"
        ),
        "chart_type": "table",
        "y_axis_unit": "short",
        "layout": {"x": 0, "y": 72, "w": 12, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},
                {"op": "SUM", "field": "gen_ai.usage.cache_creation_tokens"},
            ],
            "breakdowns": ["git.repo"],
            "orders": [{"op": "SUM", "field": "gen_ai.usage.output_tokens", "order": "descending"}],
            "limit": 20,
            "time_range": 86400,
        },
    },
    # ── Row 78 (y=78, h=6): Estimated cost per repo ─────────────────────────
    {
        "name": "Estimated Cost per Repo (USD)",
        "desc": (
            "Approximate USD cost per repo (git.repo; blank = non-git cwd or HTTPS remote). "
            "Rates (verify at anthropic.com/pricing): "
            "Sonnet $3/$15/$0.30/$3.75 per MTok input/output/cache-read/cache-creation; "
            "Haiku $0.80/$4/$0.08/$1. "
            "Formula uses Sonnet rates for all models — Haiku cost is overestimated ~3–4x."
        ),
        "chart_type": "table",
        "decimal_precision": 4,
        "layout": {"x": 0, "y": 78, "w": 12, "h": 6},
        "query": {
            "span_name": "claude_code.turn.stop",
            "aggregations": [
                {"op": "SUM", "field": "gen_ai.usage.input_tokens"},        # A
                {"op": "SUM", "field": "gen_ai.usage.output_tokens"},       # B
                {"op": "SUM", "field": "gen_ai.usage.cache_read_tokens"},   # C
                {"op": "SUM", "field": "gen_ai.usage.cache_creation_tokens"},  # D
            ],
            # Sonnet-rate formula: $3/$15/$0.30/$3.75 per MTok
            "formulas": [
                {
                    "expression": "A * 0.000003 + B * 0.000015 + C * 0.0000003 + D * 0.00000375",
                    "legend": "total_cost_usd",
                }
            ],
            "breakdowns": ["git.repo"],
            "limit": 20,
            "time_range": 86400,
        },
    },
]


# ---------------------------------------------------------------------------
# Backend adapters
# ---------------------------------------------------------------------------

_HC_OP_MAP = {
    "descending": "descending",
    "ascending": "ascending",
    "desc": "descending",
    "asc": "ascending",
}


def _existing_honeycomb_headers() -> str:
    """Return the Honeycomb OTLP headers already configured in ~/.claude/settings.json,
    or "" if absent, non-Honeycomb, or still the placeholder — so reinstalling
    never clobbers a working API key."""
    try:
        env = json.loads((HOME_CLAUDE / "settings.json").read_text()).get("env", {})
    except (OSError, json.JSONDecodeError):
        return ""
    headers = env.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    if headers.startswith("x-honeycomb-team=") and "FILL_IN" not in headers:
        return headers
    return ""


class HoneycombBackend:
    """Honeycomb backend — preserves the existing Honeycomb board workflow."""

    name = "honeycomb"

    def otel_env(self) -> dict[str, str]:
        return {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://api.honeycomb.io",
            "OTEL_EXPORTER_OTLP_HEADERS": _existing_honeycomb_headers()
                or "x-honeycomb-team=hcaik_FILL_IN_YOUR_KEY",
        }

    def _ir_to_hc_spec(self, query: dict) -> dict:
        """Translate a neutral query IR dict → Honeycomb query spec."""
        has_formulas = bool(query.get("formulas"))

        # calculations: [{op, column?, name?}]
        # Names are required when formulas are present (Honeycomb needs $name refs)
        calculations = []
        for i, agg in enumerate(query["aggregations"]):
            entry: dict[str, Any] = {"op": agg["op"]}
            if "field" in agg:
                entry["column"] = agg["field"]
            if has_formulas:
                entry["name"] = QUERY_NAMES[i]
            calculations.append(entry)

        # filters: span_name filter first, then extra filters
        filters: list[dict] = [
            {"column": "name", "op": "=", "value": query["span_name"]}
        ]
        for f in query.get("filters", []):
            filters.append({"column": f["field"], "op": f["op"], "value": f["value"]})

        spec: dict[str, Any] = {
            "calculations": calculations,
            "filters": filters,
            "time_range": query.get("time_range", 86400),
        }
        if "breakdowns" in query:
            spec["breakdowns"] = query["breakdowns"]
        if "orders" in query:
            hc_orders = []
            for o in query["orders"]:
                entry = {"op": o["op"], "order": _HC_OP_MAP.get(o.get("order", "descending"), "descending")}
                if "field" in o:
                    entry["column"] = o["field"]
                hc_orders.append(entry)
            spec["orders"] = hc_orders
        if "limit" in query:
            spec["limit"] = query["limit"]
        if has_formulas:
            hc_formulas = []
            for i, f in enumerate(query["formulas"]):
                # Translate bare letter vars (A, B, C…) → Honeycomb $-prefixed names ($A, $B…)
                expr = re.sub(r"\b([A-Z])\b", r"$\1", f["expression"])
                hc_formulas.append({"expression": expr, "name": f.get("legend", f"F{i + 1}")})
            spec["formulas"] = hc_formulas
        return spec

    def render_dashboard(self, panels: list[dict]) -> list[dict]:
        """Render panels as Honeycomb-native panel list (for honeycomb_panels.json)."""
        result = []
        for p in panels:
            panel = {
                "name": p["name"],
                "desc": p["desc"],
                "chart_type": p["chart_type"],
                **p["layout"],
                "spec": self._ir_to_hc_spec(p["query"]),
            }
            result.append(panel)
        return result

    def install_dashboard(self, dashboard: list[dict]) -> None:
        """Write rendered panels to ~/.claude/honeycomb_panels.json."""
        path = HOME_CLAUDE / "honeycomb_panels.json"
        path.write_text(json.dumps(dashboard, indent=2))
        print(f"✓ Honeycomb panel definitions written to {path}")

    def post_install_message(self) -> str:
        return (
            "To create the Honeycomb board, open Claude Code in this repo and prompt:\n"
            '  "Create a Honeycomb board called \'Claude Code Sessions\' using the\n'
            '   panel definitions in ~/.claude/honeycomb_panels.json"\n'
            "\nRequires the Honeycomb MCP server configured in Claude Code.\n"
            "\nBoard features:\n"
            "  • Time-series panels use bar charts for discrete-time visualization\n"
            "  • Activity metrics: sessions, tool calls, user prompts\n"
            "  • Performance: latency, cache hit ratio, token usage\n"
            "  • Category breakdowns: by model, tool, session, stop reason"
        )


# SigNoz aggregate operator mapping (IR op → SigNoz aggregateOperator)
_SZ_AGG_OP: dict[str, str] = {
    "COUNT": "count",
    "AVG": "avg",
    "P95": "p95",
    "P99": "p99",
    "P50": "p50",
    "SUM": "sum",
    "MAX": "max",
    "MIN": "min",
}

# IR field names that map to SigNoz built-in span fields (not custom attributes)
_SZ_FIELD_MAP: dict[str, str] = {
    "duration_ms": "duration_nano",
}

# SigNoz panel type mapping (IR chart_type → SigNoz panelTypes)
_SZ_PANEL_TYPE: dict[str, str] = {
    "bar": "bar",
    "table": "table",
    "value": "value",
}

# Static visual config shared by every SigNoz widget (v5 format). Computed
# fields (decimalPrecision, yAxisUnit) are set per-panel in _panel_to_widget.
_SZ_WIDGET_DEFAULTS: dict[str, Any] = {
    "bucketCount": 30,
    "bucketWidth": 0,
    "columnUnits": {},
    "contextLinks": {"linksData": []},
    "customLegendColors": {},
    "fillMode": "none",
    "fillSpans": False,
    "isLogScale": False,
    "legendPosition": "bottom",
    "lineInterpolation": "spline",
    "lineStyle": "solid",
    "mergeAllActiveQueries": False,
    "nullZeroValues": "zero",
    "opacity": "1",
    "selectedLogFields": [],
    "selectedTracesFields": [
        {"fieldContext": "resource", "fieldDataType": "string", "name": "service.name", "signal": "traces"},
        {"fieldContext": "span", "fieldDataType": "string", "name": "name", "signal": "traces"},
        {"fieldContext": "span", "fieldDataType": "", "name": "duration_nano", "signal": "traces"},
    ],
    "showPoints": False,
    "softMax": 0,
    "softMin": 0,
    "spanGaps": True,
    "stackedBarChart": False,
    "thresholds": [],
    "timePreferance": "GLOBAL_TIME",
}


def _sz_filter_value(v: Any) -> str:
    """Render a filter value as a SQL-like literal for SigNoz expression strings."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f"'{v}'"
    return str(v)


def _sz_order_by(o: dict, aggs: list[dict]) -> dict:
    """Translate one IR order clause → SigNoz orderBy entry.

    Uses the 0-based aggregation index as columnName: in the expression-based
    format, "count()" is NOT a valid key; only the index ("0", "1", …),
    group-by field names, and expression strings like "avg(field)" are
    accepted.  Index notation covers all cases.
    """
    order_str = "desc" if "desc" in o.get("order", "desc") else "asc"
    o_op = o.get("op", "COUNT")
    o_field = o.get("field", "")
    for idx, agg in enumerate(aggs):
        if agg["op"] == o_op and agg.get("field", "") == o_field:
            return {"columnName": str(idx), "order": order_str}
    return {"columnName": "0", "order": order_str}  # fallback


def _sz_series_legend(agg: dict, aggs: list[dict], q: dict) -> str:
    """Legend for one series of a multi-aggregation panel.

    - single agg or grouped → "" (panel title / group value is enough)
    - multiple aggs, all unique ops → label by op name ("avg", "p95")
    - multiple aggs, same op on different fields → last field component
    """
    if len(aggs) <= 1 or q.get("breakdowns"):
        return ""
    if len({a["op"] for a in aggs}) == len(aggs):
        return _SZ_AGG_OP.get(agg["op"], agg["op"].lower())
    field = agg.get("field", "")
    return field.split(".")[-1] if field else ""


def _sz_query_data(q: dict) -> list[dict]:
    """Build SigNoz queryData — one entry per aggregation (A, B, C …).

    Uses SigNoz's expression-based query builder format (the format the UI
    saves when you edit a panel manually), with expression strings for
    filters, aggregations, and having.
    """
    aggs = q["aggregations"]

    # Filter expression: "name = 'span_name' AND field op value …"
    filter_parts = [f"name = '{q['span_name']}'"]
    for f in q.get("filters", []):
        filter_parts.append(f"{f['field']} {f['op']} {_sz_filter_value(f['value'])}")
    filter_expr = {"expression": " AND ".join(filter_parts)}

    # GroupBy: SigNoz still expects key-metadata objects here (not expression strings)
    group_by = [
        {"key": b, "dataType": "string", "type": "tag", "isColumn": False, "isJSON": False}
        for b in q.get("breakdowns", [])
    ]

    order_by = [_sz_order_by(o, aggs) for o in q.get("orders", [])]

    query_data: list[dict] = []
    for i, agg in enumerate(aggs):
        agg_op = _SZ_AGG_OP.get(agg["op"], "count")
        agg_field = _SZ_FIELD_MAP.get(agg.get("field", ""), agg.get("field", ""))
        query_data.append({
            "dataSource": "traces",
            "queryName": QUERY_NAMES[i],
            "aggregations": [{"expression": f"{agg_op}({agg_field})" if agg_field else f"{agg_op}()"}],
            "filter": filter_expr,
            "groupBy": group_by,
            "expression": QUERY_NAMES[i],
            "disabled": False,
            "legend": _sz_series_legend(agg, aggs, q),
            "stepInterval": None,
            "orderBy": order_by,
            "having": {"expression": ""},
            "limit": q.get("limit", None),
            "functions": [],
            "source": "",
        })
    return query_data


def _sz_formulas(q: dict) -> list[dict]:
    """Query formulas — derived expressions over the named queries (A, B, C …).

    IR: [{"expression": "B/(A+B)", "legend": "cache hit ratio"}, …]
    """
    return [
        {
            "disabled": False,
            "expression": f["expression"],
            "legend": f.get("legend", ""),
            "queryName": f"F{i + 1}",
        }
        for i, f in enumerate(q.get("formulas", []))
    ]


class SignozBackend:
    """SigNoz backend — self-hosted OTLP at http://localhost:4318."""

    name = "signoz"

    def __init__(self, args: argparse.Namespace) -> None:
        self.signoz_url: str = getattr(args, "signoz_url", "http://localhost:8080")
        self.api_key: str | None = getattr(args, "signoz_api_key", None)
        self._dashboard_id: str | None = None

    def otel_env(self) -> dict[str, str]:
        # Empty OTEL_EXPORTER_OTLP_HEADERS is safe: otel_span.py filters on "=" presence
        return {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_EXPORTER_OTLP_HEADERS": "",
        }

    def _panel_to_widget(self, panel: dict) -> dict:
        """Translate one IR panel → SigNoz v5 dashboard widget object.

        Returns a dict with a '_layout' key (popped by render_dashboard into
        the dashboard-level layout array) plus all widget fields.
        """
        q = panel["query"]
        widget_id = str(uuid.uuid4())
        layout = panel["layout"]

        # decimalPrecision: panel override → 0 for counts/sums → 2 for avg/percentiles
        first_agg_op = q["aggregations"][0]["op"] if q["aggregations"] else "COUNT"
        decimal_precision = panel.get(
            "decimal_precision",
            0 if first_agg_op in ("COUNT", "SUM") else 2,
        )

        return {
            "id": widget_id,
            "title": panel["name"],
            "description": panel["desc"],
            "panelTypes": _SZ_PANEL_TYPE.get(panel["chart_type"], "graph"),
            **_SZ_WIDGET_DEFAULTS,
            "decimalPrecision": decimal_precision,
            "yAxisUnit": panel.get("y_axis_unit", ""),
            "query": {
                "id": str(uuid.uuid4()),
                "queryType": "builder",
                "unit": "",
                "promql": [{"query": "", "legend": "", "disabled": False, "name": "A"}],
                "clickhouse_sql": [{"query": "", "legend": "", "disabled": False, "name": "A"}],
                "builder": {
                    "queryData": _sz_query_data(q),
                    "queryFormulas": _sz_formulas(q),
                    "queryTraceOperator": [],
                },
            },
            "_layout": {
                "h": layout["h"],
                "i": widget_id,
                "moved": False,
                "static": False,
                "w": layout["w"],
                "x": layout["x"],
                "y": layout["y"],
            },
        }

    def render_dashboard(self, panels: list[dict]) -> dict:
        """Build a SigNoz v5 dashboard dict from IR panels."""
        widgets: list[dict] = []
        layouts: list[dict] = []
        for p in panels:
            widget = self._panel_to_widget(p)
            layouts.append(widget.pop("_layout"))
            widgets.append(widget)
        return {
            "title": "Claude Code Sessions",
            "description": "OpenTelemetry instrumentation for Claude Code sessions via hooks",
            "tags": ["claude-code"],
            "version": "v5",
            "layout": layouts,
            "panelMap": {},
            "uploadedGrafana": False,
            "widgets": widgets,
            "time": {
                "isRelative": True,
                "relativeTime": "1d",
                "startTime": 0,
                "endTime": 0,
            },
        }

    def install_dashboard(self, dashboard: dict) -> None:
        """Write dashboard JSON and optionally POST to SigNoz API."""
        path = HOME_CLAUDE / "signoz_dashboard.json"
        path.write_text(json.dumps(dashboard, indent=2))
        print(f"✓ SigNoz dashboard JSON written to {path}")

        if self.api_key:
            self._post_dashboard(dashboard)

    def _post_dashboard(self, dashboard: dict) -> None:
        url = f"{self.signoz_url}/api/v1/dashboards"
        data = json.dumps(dashboard).encode()
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "SIGNOZ-API-KEY": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
                dash_id = body.get("data", {}).get("uuid") or body.get("data", {}).get("id") or body.get("id", "?")
                self._dashboard_id = dash_id
                dash_url = f"{self.signoz_url}/dashboard/{dash_id}?relativeTime=1d"
                print(f"✓ Dashboard created via SigNoz API (ID: {dash_id})")
                print(f"  Open: {dash_url}")
        except urllib.error.HTTPError as e:
            print(f"✗ SigNoz API POST failed ({e.code}): {e.read().decode()}")
            print("  Import manually via: Dashboards → New Dashboard → Import JSON")
        except Exception as e:
            print(f"✗ SigNoz API POST failed: {e}")
            print("  Import manually via: Dashboards → New Dashboard → Import JSON")

    def post_install_message(self) -> str:
        msg = (
            "SigNoz dashboard JSON written to ~/.claude/signoz_dashboard.json\n"
            "\nTo import manually:\n"
            "  SigNoz UI → Dashboards → New Dashboard → Import JSON\n"
            "  Paste the contents of ~/.claude/signoz_dashboard.json\n"
        )
        if self._dashboard_id:
            dash_url = f"{self.signoz_url}/dashboard/{self._dashboard_id}?relativeTime=1d"
            msg += f"\nDashboard URL (opens to last 1 day):\n  {dash_url}\n"
            msg += "\n  Opening this URL once sets the 1-day default in your browser.\n"
        return msg


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Claude Code hooks with OTel backend selection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--backend",
        choices=["honeycomb", "signoz"],
        default="honeycomb",
        help="OTel backend to configure (default: honeycomb)",
    )
    parser.add_argument(
        "--signoz-url",
        default="http://localhost:8080",
        help="SigNoz base URL for API POST (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--signoz-api-key",
        default=os.environ.get("SIGNOZ_API_KEY"),
        help="SigNoz API key — also reads SIGNOZ_API_KEY env var",
    )
    args = parser.parse_args()

    if args.backend == "honeycomb":
        backend: HoneycombBackend | SignozBackend = HoneycombBackend()
    else:
        backend = SignozBackend(args)

    install_hooks()
    merge_settings(backend)
    dashboard = backend.render_dashboard(PANELS)
    backend.install_dashboard(dashboard)

    print()
    print(f"Hooks and settings installed (backend: {backend.name}).")
    print()
    print(backend.post_install_message())


if __name__ == "__main__":
    main()
