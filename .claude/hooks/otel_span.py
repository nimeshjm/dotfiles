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


def _get_exporter() -> "OTLPSpanExporter | None":
    if not _OTEL_AVAILABLE:
        return None
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    headers_raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    headers: dict[str, str] = {}
    for part in headers_raw.split(","):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            headers[k.strip()] = v.strip()
    return OTLPSpanExporter(endpoint=endpoint, headers=headers)


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

    service_name = os.environ.get("OTEL_SERVICE_NAME", "claude-code-interactive")
    resource = Resource.create({
        "service.name": service_name,
        "gen_ai.system": "anthropic",
    })

    exporter = _get_exporter()
    if exporter is None:
        return

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("claude_code_hooks", "1.0.0")

    now_ns = time.time_ns()
    start = start_time_ns or now_ns
    end   = end_time_ns   or now_ns

    # Sanitise: OTel attribute values must be str/int/float/bool or lists thereof
    clean: dict[str, Any] = {}
    for k, v in attributes.items():
        if isinstance(v, (str, int, float, bool)):
            clean[k] = v
        elif v is None:
            clean[k] = ""
        else:
            clean[k] = str(v)

    with tracer.start_as_current_span(
        name,
        kind=SpanKind.INTERNAL,
        start_time=start,
        attributes=clean,
    ) as span:
        if not status_ok:
            span.set_status(StatusCode.ERROR, error_message)
        else:
            span.set_status(StatusCode.OK)
        # manually set end time
        span._end_time = end  # type: ignore[attr-defined]

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
