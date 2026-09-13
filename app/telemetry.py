"""Logging, redaction, tracing, and metrics for the mcp-gateway.

Everything a caller needs is in the frozen public interface documented in
``docs/cloud/CONTRACTS.md`` Section 1 (the ``GATEWAY_*`` telemetry names) and
in the work-package brief: ``configure_logging``, ``RedactingFilter``,
``redact``, ``configure_telemetry``/``Telemetry``, ``asgi_middleware``,
``ToolSpanMiddleware``, ``current_request_id``, ``configure_from_env``.

No secret values, tool arguments, or tool results are ever recorded in a
span, a metric, or a log line -- only names, outcomes, counts, and timings.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

import fastmcp.server.middleware as fastmcp_middleware
from fastmcp.exceptions import ToolError
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import (
    Status,
    StatusCode,
    SpanKind,
    format_span_id,
    format_trace_id,
    get_current_span,
)
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("mcp_gateway.telemetry")

__all__ = [
    "configure_logging",
    "RedactingFilter",
    "redact",
    "configure_telemetry",
    "Telemetry",
    "asgi_middleware",
    "RequestContextMiddleware",
    "ToolSpanMiddleware",
    "current_request_id",
    "configure_from_env",
]

# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_SENSITIVE_KEYS = (
    "authorization",
    "cookie",
    "set-cookie",
    "code",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "client_id",
    "signing_key",
    "jwt_signing_key",
    "password",
    "secret",
    "api_key",
)
_KEY_ALTERNATION = "|".join(re.escape(key) for key in _SENSITIVE_KEYS)

# "key": "value"  (JSON-ish)
_RE_JSON_KV = re.compile(
    rf'(?i)"({_KEY_ALTERNATION})"\s*:\s*"([^"]*)"'
)
# key=value  (query-string / logfmt-ish, value runs to next delimiter)
_RE_EQ_KV = re.compile(
    rf'(?i)\b({_KEY_ALTERNATION})\b\s*=\s*([^\s,;&"\']+)'
)
# key: value  (header-ish, value runs to end of line or a delimiter)
_RE_COLON_KV = re.compile(
    rf'(?i)\b({_KEY_ALTERNATION})\b\s*:\s*([^\n,;"\']+)'
)

_SHAPE_PATTERNS = [
    re.compile(r"gho_[A-Za-z0-9]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"ghu_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])"),
]

_REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """Scrub known-sensitive key/value pairs and secret-shaped substrings."""
    if not isinstance(text, str) or not text:
        return text

    def _json_sub(match: re.Match) -> str:
        return f'"{match.group(1)}": "{_REDACTED}"'

    def _eq_sub(match: re.Match) -> str:
        return f"{match.group(1)}={_REDACTED}"

    def _colon_sub(match: re.Match) -> str:
        return f"{match.group(1)}: {_REDACTED}"

    out = _RE_JSON_KV.sub(_json_sub, text)
    out = _RE_EQ_KV.sub(_eq_sub, out)
    out = _RE_COLON_KV.sub(_colon_sub, out)
    for pattern in _SHAPE_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def _redact_keyed_value(key: str, value: object) -> str:
    """Redact a single (key, value) pair, as opposed to free text."""
    if key.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    return redact(str(value))


class RedactingFilter(logging.Filter):
    """Scrubs a LogRecord's message, args, and extra fields in place."""

    _STANDARD_ATTRS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
        }
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            # Deferred %-style formatting: redact the substituted VALUES, not
            # the template. Redacting `record.msg` here too would risk eating
            # a "%s" placeholder that happens to sit right after a sensitive
            # key (e.g. "token=%s") before it is ever substituted, which
            # leaves `record.args` with a value nothing will consume.
            if isinstance(record.args, Mapping):
                record.args = {k: _redact_keyed_value(k, v) for k, v in record.args.items()}
            else:
                record.args = tuple(
                    redact(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        elif isinstance(record.msg, str):
            # No deferred args: `record.msg` is already the complete message
            # (e.g. an f-string), so it is safe to redact directly.
            record.msg = redact(record.msg)
        for key in list(record.__dict__.keys()):
            if key in self._STANDARD_ATTRS or key.startswith("_"):
                continue
            record.__dict__[key] = _redact_keyed_value(key, record.__dict__[key])
        return True


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": current_request_id(),
            "trace_id": None,
            "span_id": None,
        }
        span = get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and ctx.is_valid:
            payload["trace_id"] = format_trace_id(ctx.trace_id)
            payload["span_id"] = format_span_id(ctx.span_id)
        for key, value in record.__dict__.items():
            if key in RedactingFilter._STANDARD_ATTRS or key.startswith("_") or key == "message":
                continue
            if key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Belt-and-suspenders: redact the fully rendered message one more
        # time, in case a secret only became part of the text once the
        # deferred %-args were substituted in.
        payload["msg"] = redact(payload["msg"]) if isinstance(payload["msg"], str) else payload["msg"]
        return json.dumps(payload, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_THIRD_PARTY_LOGGERS = ("fastmcp", "FastMCP", "uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging(fmt: str = "json", level: str = "INFO") -> None:
    """Configure the root logger to write to stderr, redacted, once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level)

    # Third-party libraries (FastMCP installs rich console handlers at import
    # time) must not bypass the JSON formatter or the redaction filter.
    for name in _THIRD_PARTY_LOGGERS:
        third_party = logging.getLogger(name)
        for extra in list(third_party.handlers):
            third_party.removeHandler(extra)
        third_party.propagate = True


# --------------------------------------------------------------------------
# Request correlation
# --------------------------------------------------------------------------

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gateway_request_id", default=None
)

_ROUTE_ALLOWLIST = frozenset(
    {"/mcp", "/healthz", "/readyz", "/authorize", "/token", "/auth/callback"}
)


def current_request_id() -> str | None:
    return _request_id_var.get()


def _route_label(path: str) -> str:
    if path in _ROUTE_ALLOWLIST:
        return path
    if path.startswith("/.well-known/"):
        return "/.well-known/*"
    return "other"


def _set_attribute(span, key: str, value) -> None:
    """Set a span attribute, redacting it first when it is a string.

    Defense in depth: none of the attributes this module sets are expected
    to ever carry a secret, but ``gateway.request_id`` is user-supplied
    (via ``X-Request-ID``), so every string attribute goes through
    ``redact`` before it is recorded, not only at export time.
    """
    if isinstance(value, str):
        value = _redact_keyed_value(key, value)
    span.set_attribute(key, value)


def _status_class(status_code: int) -> str:
    bucket = status_code // 100
    if 1 <= bucket <= 5:
        return f"{bucket}xx"
    return "5xx"


class RequestContextMiddleware:
    """Pure ASGI middleware: request-id correlation + one SERVER span."""

    def __init__(self, app: ASGIApp, telemetry: "Telemetry") -> None:
        self.app = app
        self._telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"x-request-id")
        candidate = raw.decode("latin-1") if raw else None
        request_id = (
            candidate if candidate and _REQUEST_ID_RE.match(candidate) else str(uuid.uuid4())
        )
        token = _request_id_var.set(request_id)

        method = scope.get("method", "")
        route = _route_label(scope.get("path", ""))
        status_holder = {"code": 500}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                headers_out = list(message.get("headers", []))
                headers_out.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers_out}
            await send(message)

        start = time.perf_counter()
        tracer = self._telemetry.tracer
        try:
            with tracer.start_as_current_span("http.request", kind=SpanKind.SERVER) as span:
                _set_attribute(span, "http.request.method", method)
                _set_attribute(span, "http.route", route)
                _set_attribute(span, "gateway.request_id", request_id)
                try:
                    await self.app(scope, receive, send_wrapper)
                except Exception:
                    span.set_status(Status(StatusCode.ERROR))
                    raise
                finally:
                    _set_attribute(span, "http.response.status_code", status_holder["code"])
        finally:
            duration = time.perf_counter() - start
            self._telemetry.record_http_request(
                method, route, _status_class(status_holder["code"]), duration
            )
            _request_id_var.reset(token)


def asgi_middleware(telemetry: "Telemetry") -> list:
    """Middleware list for ``FastMCP(...).http_app(middleware=asgi_middleware(t))``."""
    return [StarletteMiddleware(RequestContextMiddleware, telemetry=telemetry)]


# --------------------------------------------------------------------------
# Tool spans
# --------------------------------------------------------------------------


class ToolSpanMiddleware(fastmcp_middleware.Middleware):
    """Wraps every tool call in a span + metrics; never records arguments."""

    def __init__(self, telemetry: "Telemetry") -> None:
        self._telemetry = telemetry

    async def on_call_tool(self, context, call_next):
        tool_name = getattr(context.message, "name", None) or "unknown"
        tracer = self._telemetry.tracer
        start = time.perf_counter()
        outcome = "ok"
        with tracer.start_as_current_span("mcp.tool") as span:
            _set_attribute(span, "mcp.tool.name", tool_name)
            try:
                return await call_next(context)
            except ToolError as exc:
                message = str(exc)
                if message.startswith("Unknown tool") or message.startswith("Not authorized"):
                    outcome = "denied"
                else:
                    outcome = "error"
                raise
            except Exception:
                outcome = "error"
                raise
            finally:
                _set_attribute(span, "gateway.outcome", outcome)
                duration_ms = (time.perf_counter() - start) * 1000
                self._telemetry.record_tool_call(tool_name, outcome)
                logger.info(
                    "tool call",
                    extra={
                        "tool": tool_name,
                        "outcome": outcome,
                        "duration_ms": round(duration_ms, 3),
                    },
                )


# --------------------------------------------------------------------------
# Exporters (stdout)
# --------------------------------------------------------------------------


def _ns_to_iso(nanoseconds: int | None) -> str | None:
    if not nanoseconds:
        return None
    return datetime.fromtimestamp(nanoseconds / 1e9, tz=timezone.utc).isoformat()


def _span_to_dict(span: ReadableSpan) -> dict:
    ctx = span.get_span_context()
    parent_id = format_span_id(span.parent.span_id) if span.parent else None
    attributes = {
        str(key): _redact_keyed_value(str(key), value)
        for key, value in (span.attributes or {}).items()
    }
    return {
        "otel": "span",
        "name": span.name,
        "trace_id": format_trace_id(ctx.trace_id),
        "span_id": format_span_id(ctx.span_id),
        "parent_id": parent_id,
        "start": _ns_to_iso(span.start_time),
        "end": _ns_to_iso(span.end_time),
        "attributes": attributes,
        "status": span.status.status_code.name if span.status else None,
    }


class _StdoutSpanExporter(SpanExporter):
    """Writes one compact JSON line per finished span to a stream."""

    def __init__(self, stream=None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def export(self, spans: Iterable[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            self._stream.write(json.dumps(_span_to_dict(span)) + "\n")
        self._stream.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - trivial
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _StdoutMetricExporter(MetricExporter):
    """Writes one compact JSON line per metrics export to a stream."""

    def __init__(self, stream=None) -> None:
        super().__init__()
        self._stream = stream if stream is not None else sys.stderr

    def export(
        self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs
    ) -> MetricExportResult:
        points = []
        for resource_metrics in metrics_data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    for point in getattr(metric.data, "data_points", []):
                        value = getattr(point, "value", None)
                        if value is None:
                            value = getattr(point, "sum", None)
                        attrs = {
                            str(key): _redact_keyed_value(str(key), val)
                            for key, val in (point.attributes or {}).items()
                        }
                        points.append({"name": metric.name, "attributes": attrs, "value": value})
        line = json.dumps(
            {"otel": "metrics", "at": datetime.now(timezone.utc).isoformat(), "points": points}
        )
        self._stream.write(line + "\n")
        self._stream.flush()
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:  # pragma: no cover
        pass


_VALID_EXPORTERS = frozenset({"stdout", "otlp", "none", "memory"})
_VALID_RELEASE_OUTCOMES = frozenset({"ready", "not_ready", "rolled_back"})


# --------------------------------------------------------------------------
# Telemetry handle
# --------------------------------------------------------------------------


class Telemetry:
    """Holds the tracer/meter providers and the gateway's instruments."""

    def __init__(self, *, tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.tracer = tracer_provider.get_tracer("mcp-gateway")
        self.meter = meter_provider.get_meter("mcp-gateway")

        self._readiness_probe: Callable[[], dict[str, bool]] | None = None

        self._http_requests = self.meter.create_counter(
            "gateway.http.requests", description="HTTP requests received"
        )
        self._http_duration = self.meter.create_histogram(
            "gateway.http.request.duration", unit="s", description="HTTP request duration"
        )
        self._tool_calls = self.meter.create_counter(
            "gateway.tool.calls", description="MCP tool calls"
        )
        self._authz_denials = self.meter.create_counter(
            "gateway.authz.denials", description="Authorization denials"
        )
        self._release_outcomes = self.meter.create_counter(
            "gateway.release.outcomes", description="Release outcomes"
        )
        self._ready_gauge = self.meter.create_observable_gauge(
            "gateway.ready",
            callbacks=[self._observe_ready],
            description="1 if the readiness probe reports all checks true, else 0",
        )

    # -- readiness -----------------------------------------------------

    def set_readiness_probe(self, probe: Callable[[], dict[str, bool]] | None) -> None:
        self._readiness_probe = probe

    def _observe_ready(self, options: CallbackOptions) -> Iterable[Observation]:
        probe = self._readiness_probe
        if probe is None:
            value = 0
        else:
            checks = probe() or {}
            value = 1 if checks and all(checks.values()) else 0
        yield Observation(value)

    # -- recording -------------------------------------------------------

    def record_release_outcome(self, outcome: str) -> None:
        if outcome not in _VALID_RELEASE_OUTCOMES:
            raise ValueError(f"unknown release outcome: {outcome!r}")
        self._release_outcomes.add(1, {"outcome": outcome})

    def record_http_request(
        self, method: str, route: str, status_class: str, duration_seconds: float
    ) -> None:
        self._http_requests.add(1, {"method": method, "route": route, "status_class": status_class})
        self._http_duration.record(duration_seconds, {"route": route})

    def record_tool_call(self, tool: str, outcome: str) -> None:
        self._tool_calls.add(1, {"tool": tool, "outcome": outcome})
        if outcome == "denied":
            self._authz_denials.add(1, {"tool": tool})

    # -- lifecycle ---------------------------------------------------------

    def shutdown(self) -> None:
        try:
            self.tracer_provider.shutdown()
        finally:
            self.meter_provider.shutdown()


def configure_telemetry(
    *,
    service_name: str = "mcp-gateway",
    release_id: str = "dev",
    exporter: str = "stdout",
    sample_ratio: float = 1.0,
    metrics_interval_seconds: int = 60,
) -> Telemetry:
    if exporter not in _VALID_EXPORTERS:
        raise ValueError(f"exporter must be one of {sorted(_VALID_EXPORTERS)}: {exporter!r}")
    if not (0.0 <= sample_ratio <= 1.0):
        raise ValueError(f"sample_ratio must be between 0.0 and 1.0: {sample_ratio!r}")
    if metrics_interval_seconds <= 0:
        raise ValueError(
            f"metrics_interval_seconds must be positive: {metrics_interval_seconds!r}"
        )

    resource = Resource.create({"service.name": service_name, "service.version": release_id})
    sampler = ParentBased(TraceIdRatioBased(sample_ratio))
    tracer_provider = TracerProvider(resource=resource, sampler=sampler, shutdown_on_exit=False)

    span_exporter_handle = None
    if exporter == "stdout":
        tracer_provider.add_span_processor(SimpleSpanProcessor(_StdoutSpanExporter()))
    elif exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif exporter == "memory":
        span_exporter_handle = InMemorySpanExporter()
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter_handle))
    # "none": no span processor at all.

    metric_reader_handle = None
    readers = []
    if exporter == "stdout":
        readers.append(
            PeriodicExportingMetricReader(
                _StdoutMetricExporter(), export_interval_millis=metrics_interval_seconds * 1000
            )
        )
    elif exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(), export_interval_millis=metrics_interval_seconds * 1000
            )
        )
    elif exporter == "memory":
        metric_reader_handle = InMemoryMetricReader()
        readers.append(metric_reader_handle)
    # "none": no metric readers.

    meter_provider = MeterProvider(resource=resource, metric_readers=readers, shutdown_on_exit=False)

    telemetry = Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider)
    if span_exporter_handle is not None:
        telemetry.span_exporter = span_exporter_handle
    if metric_reader_handle is not None:
        telemetry.metric_reader = metric_reader_handle
    return telemetry


# --------------------------------------------------------------------------
# Env-driven construction
# --------------------------------------------------------------------------


def configure_from_env(environ: Mapping[str, str]) -> Telemetry:
    """Read the GATEWAY_* telemetry names, validate them, wire up logging + telemetry."""
    fmt = environ.get("GATEWAY_LOG_FORMAT", "json")
    level = environ.get("GATEWAY_LOG_LEVEL", "INFO")
    exporter = environ.get("GATEWAY_OTEL_EXPORTER", "stdout")
    release_id = environ.get("GATEWAY_RELEASE_ID", "dev")

    ratio_raw = environ.get("GATEWAY_TRACE_SAMPLE_RATIO", "1.0")
    try:
        sample_ratio = float(ratio_raw)
    except ValueError as exc:
        raise ValueError(
            f"GATEWAY_TRACE_SAMPLE_RATIO must be a float, got {ratio_raw!r}"
        ) from exc
    if not (0.0 <= sample_ratio <= 1.0):
        raise ValueError(
            f"GATEWAY_TRACE_SAMPLE_RATIO must be between 0.0 and 1.0, got {sample_ratio!r}"
        )

    interval_raw = environ.get("GATEWAY_METRICS_INTERVAL_SECONDS", "60")
    try:
        metrics_interval_seconds = int(interval_raw)
    except ValueError as exc:
        raise ValueError(
            f"GATEWAY_METRICS_INTERVAL_SECONDS must be an integer, got {interval_raw!r}"
        ) from exc
    if metrics_interval_seconds <= 0:
        raise ValueError(
            f"GATEWAY_METRICS_INTERVAL_SECONDS must be positive, got {metrics_interval_seconds!r}"
        )

    if exporter not in ("stdout", "otlp", "none"):
        raise ValueError(
            f"GATEWAY_OTEL_EXPORTER must be one of stdout/otlp/none, got {exporter!r}"
        )

    configure_logging(fmt=fmt, level=level)
    return configure_telemetry(
        release_id=release_id,
        exporter=exporter,
        sample_ratio=sample_ratio,
        metrics_interval_seconds=metrics_interval_seconds,
    )
