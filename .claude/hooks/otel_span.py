#!/usr/bin/env python3
"""
otel_span.py — shared helper used by every hook script.

Reads OTLP config from env vars (same ones Claude Code already uses for its
own built-in telemetry) and emits a single OTLP span over HTTP/protobuf.

No third-party deps beyond what ships with Python 3.8+, except for the
opentelemetry packages which are installed once (see README).

Install once:
    pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
"""
from __future__ import annotations

import os
import sys
import time
import json
import subprocess
from typing import Any

# ── OTel imports ──────────────────────────────────────────────────────────────
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace import SpanKind, StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


def _load_settings_env() -> None:
    """
    Hook subprocesses don't inherit the env block from settings.json — Claude Code
    uses those vars internally but doesn't inject them into child processes.
    This fallback reads settings.json (then settings.local.json) and injects any
    OTEL_* vars that aren't already in the environment.
    """
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return  # already set; nothing to do

    settings_dir = os.path.expanduser("~/.claude")
    merged: dict[str, str] = {}
    for fname in ("settings.json", "settings.local.json"):
        try:
            with open(os.path.join(settings_dir, fname)) as f:
                data = json.load(f)
            for k, v in data.get("env", {}).items():
                merged[k] = str(v)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    for k, v in merged.items():
        if k.startswith("OTEL_") and k not in os.environ:
            os.environ[k] = v


def _get_exporter() -> "OTLPSpanExporter | None":
    if not _OTEL_AVAILABLE:
        return None
    _load_settings_env()
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""):
        return None
    # Let the SDK read endpoint/headers from env vars — it correctly appends /v1/traces
    return OTLPSpanExporter()

def get_git_context(cwd: str = "") -> dict[str, str]:
    """
    Returns git repo name and remote origin URL for the given working directory.
    Falls back to empty strings if git isn't available or cwd isn't a repo.
    """
    git_attrs: dict[str, str] = {}
    run_dir = cwd or os.getcwd()

    try:
        origin = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=run_dir,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
        git_attrs["git.origin"] = origin

        # Derive a clean repo name from the URL
        # handles both https://github.com/org/repo.git and git@github.com:org/repo.git
        repo_name = origin.rstrip("/")
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        repo_name = repo_name.split("/")[-1].split(":")[-1]
        git_attrs["git.repo"] = repo_name

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return git_attrs

def emit_span(
    name: str,
    attributes: dict[str, Any],
    *,
    start_time_ns: int | None = None,
    end_time_ns: int | None = None,
    status_ok: bool = True,
    error_message: str = "",
) -> None:
    """
    Fire-and-forget: create one span, export it, done.
    Safe to call from short-lived hook scripts.
    """
    if not _OTEL_AVAILABLE:
        # Graceful degradation: just print to stderr so Claude Code shows it
        print(f"[otel_span] OTel not available — span '{name}' not exported", file=sys.stderr)
        return

    # Must run before reading any OTEL_* env vars — hooks don't inherit them from Claude Code
    _load_settings_env()

    exporter = _get_exporter()
    if exporter is None:
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "claude-code-interactive")
    resource = Resource.create({
        "service.name": service_name,
        "gen_ai.system": "anthropic",
    })

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("claude_code_hooks", "1.0.0")

    now_ns = time.time_ns()
    start = start_time_ns or now_ns
    end   = end_time_ns   or now_ns

    # merge git context -- uses cwd already in attributes if present
    git_attrs = get_git_context(attributes.get("cwd", ""))
    attributes = {**git_attrs, **attributes}   # hook attrs win on collision

    # Sanitise: OTel attribute values must be str/int/float/bool or lists thereof
    clean: dict[str, Any] = {}
    for k, v in attributes.items():
        if isinstance(v, (str, int, float, bool)):
            clean[k] = v
        elif v is None:
            clean[k] = ""
        else:
            clean[k] = str(v)

    span = tracer.start_span(name, kind=SpanKind.INTERNAL, start_time=start, attributes=clean)
    if not status_ok:
        span.set_status(StatusCode.ERROR, error_message)
    else:
        span.set_status(StatusCode.OK)
    span.end(end_time=end)

    provider.force_flush(timeout_millis=3_000)
    provider.shutdown()


def read_stdin() -> dict[str, Any]:
    """Read and parse the JSON that Claude Code sends on stdin."""
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    # Quick smoke-test: python otel_span.py
    emit_span("test.hook", {"test": True, "note": "smoke test from otel_span.py"})
    print("Span emitted (or gracefully skipped).")
