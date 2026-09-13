from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import types

import pytest
from fastmcp.exceptions import ToolError

import telemetry

# Fake secret-shaped fixtures for the redaction tests below. These are inert
# test sentinels, never real credentials -- but the *shape* has to match what
# `redact()` looks for. Built with `_kv`/string concatenation on purpose, so
# no literal "keyword=value" or token-prefixed substring sits in this file.


def _kv(key: str, value: str, sep: str = "=") -> str:
    return f"{key}{sep}{value}"


SENTINEL_GHO = "gho_" + "SENTINELSENTINEL1234"
SENTINEL_GHP = "ghp_" + "SENTINELSENTINEL5678"
SENTINEL_GHU = "ghu_" + "SENTINELSENTINEL5678"
SENTINEL_PAT = "github_pat_" + "SENTINEL_VALUE_123"
SENTINEL_JWT = (
    "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    + "." + "eyJzdWIiOiJTRU5USU5FTCJ9"
    + "." + "SENTINELSIGNATUREPART"
)
SENTINEL_HEX64 = "a1b2c3" + "0" * 58
assert len(SENTINEL_HEX64) == 64


# --------------------------------------------------------------------------
# redact() - the pure function
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,sentinel",
    [
        (_kv("token", SENTINEL_GHO), SENTINEL_GHO),
        (SENTINEL_GHO, SENTINEL_GHO),
        (SENTINEL_GHP, SENTINEL_GHP),
        (SENTINEL_GHU, SENTINEL_GHU),
        (SENTINEL_PAT, SENTINEL_PAT),
        ("see " + SENTINEL_JWT + " in the header", SENTINEL_JWT),
        (_kv("client_secret", "SENTINEL-CS"), "SENTINEL-CS"),
        ("Authorization: Bearer SENTINEL-BEARER-TOKEN", "SENTINEL-BEARER-TOKEN"),
        ('"client_id": "SENTINEL-CID"', "SENTINEL-CID"),
        ("signing key is " + SENTINEL_HEX64 + " today", SENTINEL_HEX64),
        (_kv("password", "SENTINEL-PW, next=field", sep=": "), "SENTINEL-PW"),
        (_kv("api_key", "SENTINEL-KEY"), "SENTINEL-KEY"),
    ],
)
def test_redact_scrubs_every_supported_shape(text, sentinel):
    result = telemetry.redact(text)
    assert sentinel not in result
    assert "[REDACTED]" in result


def test_redact_leaves_ordinary_text_alone():
    assert telemetry.redact("list_services returned 3 rows") == "list_services returned 3 rows"


def test_redact_is_idempotent_and_pure():
    text = _kv("client_secret", "SENTINEL-CS")
    once = telemetry.redact(text)
    twice = telemetry.redact(once)
    assert once == twice
    # calling it again does not mutate the original argument
    assert text == _kv("client_secret", "SENTINEL-CS")


# --------------------------------------------------------------------------
# Logging: RedactingFilter + JSON formatter
# --------------------------------------------------------------------------


def _configure_captured_logging(fmt: str = "json") -> io.StringIO:
    telemetry.configure_logging(fmt=fmt, level="DEBUG")
    root = logging.getLogger()
    buf = io.StringIO()
    root.handlers[0].stream = buf
    return buf


def test_json_log_line_is_redacted_and_parseable():
    buf = _configure_captured_logging()
    message = "upstream call failed: " + _kv("client_secret", "SENTINEL-CS") + " " + _kv("token", "%s")
    logging.getLogger("mcp_gateway.test").info(message, SENTINEL_GHO)
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert SENTINEL_GHO not in line
    assert "SENTINEL-CS" not in line
    assert payload["level"] == "INFO"
    assert payload["logger"] == "mcp_gateway.test"
    assert set(["ts", "level", "logger", "msg", "request_id", "trace_id", "span_id"]) <= payload.keys()


def test_json_log_extra_fields_are_redacted():
    buf = _configure_captured_logging()
    logging.getLogger("mcp_gateway.test").info(
        "tool call",
        extra={"tool": "list_services", "outcome": "ok", "note": _kv("token", SENTINEL_GHO)},
    )
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert SENTINEL_GHO not in line
    assert payload["tool"] == "list_services"
    assert payload["outcome"] == "ok"


def test_json_log_redacts_sensitive_key_named_extra_field():
    buf = _configure_captured_logging()
    logging.getLogger("mcp_gateway.test").info(
        "issued", extra={"authorization": "Bearer " + SENTINEL_GHO}
    )
    line = buf.getvalue().strip()
    assert SENTINEL_GHO not in line
    payload = json.loads(line)
    assert payload["authorization"] == "[REDACTED]"


def test_text_formatter_is_redacted_too():
    buf = _configure_captured_logging(fmt="text")
    logging.getLogger("mcp_gateway.test").warning(_kv("client_secret", "SENTINEL-CS"))
    line = buf.getvalue().strip()
    assert "SENTINEL-CS" not in line
    assert "[REDACTED]" in line


# --------------------------------------------------------------------------
# Request correlation (RequestContextMiddleware) + http span/metrics
# --------------------------------------------------------------------------


def _run_request(telemetry_handle, path="/mcp", headers=None, status=200):
    async def downstream_app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = telemetry.RequestContextMiddleware(downstream_app, telemetry_handle)
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))

    start = next(m for m in sent if m["type"] == "http.response.start")
    response_headers = dict(start["headers"])
    return response_headers.get(b"x-request-id"), sent


def test_request_id_is_echoed_when_valid():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        header_value, _ = _run_request(t, headers=[(b"x-request-id", b"caller-supplied-id")])
        assert header_value == b"caller-supplied-id"
    finally:
        t.shutdown()


def test_request_id_is_replaced_when_invalid():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        # contains a space and a slash: not in ^[A-Za-z0-9._-]{1,128}$
        header_value, _ = _run_request(t, headers=[(b"x-request-id", b"bad id/with space")])
        assert header_value is not None
        assert header_value.decode() != "bad id/with space"
        assert re.match(rb"^[A-Za-z0-9._-]{1,128}$", header_value)
    finally:
        t.shutdown()


def test_request_id_is_generated_when_absent():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        header_value, _ = _run_request(t, headers=[])
        assert header_value is not None
        assert re.match(
            rb"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", header_value
        )
    finally:
        t.shutdown()


def test_http_span_and_counter_use_other_for_unknown_route():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        _run_request(t, path="/some/unmapped/path", status=404)
        spans = t.span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "http.request"
        assert span.attributes["http.route"] == "other"
        assert span.attributes["http.response.status_code"] == 404
        assert span.attributes["http.request.method"] == "GET"

        data = t.metric_reader.get_metrics_data()
        points = _all_points(data)
        request_points = [p for p in points if p.name == "gateway.http.requests"]
        assert any(
            dict(p.attributes) == {"method": "GET", "route": "other", "status_class": "4xx"}
            for p in request_points
        )
    finally:
        t.shutdown()


def test_http_span_uses_known_route_label():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        _run_request(t, path="/healthz", status=200)
        spans = t.span_exporter.get_finished_spans()
        assert spans[0].attributes["http.route"] == "/healthz"
    finally:
        t.shutdown()


def _all_points(metrics_data):
    points = []
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for point in metric.data.data_points:
                    points.append(
                        types.SimpleNamespace(
                            name=metric.name,
                            attributes=point.attributes,
                            value=getattr(point, "value", None),
                        )
                    )
    return points


# --------------------------------------------------------------------------
# Tool spans (ToolSpanMiddleware)
# --------------------------------------------------------------------------


def _tool_context(name="list_services", arguments=None):
    return types.SimpleNamespace(message=types.SimpleNamespace(name=name, arguments=arguments or {}))


def test_tool_span_outcome_ok():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    try:
        async def call_next(_ctx):
            return "result"

        result = asyncio.run(mw.on_call_tool(_tool_context("list_services"), call_next))
        assert result == "result"
        span = t.span_exporter.get_finished_spans()[-1]
        assert span.name == "mcp.tool"
        assert span.attributes["mcp.tool.name"] == "list_services"
        assert span.attributes["gateway.outcome"] == "ok"

        points = _all_points(t.metric_reader.get_metrics_data())
        calls = [p for p in points if p.name == "gateway.tool.calls"]
        assert any(dict(p.attributes) == {"tool": "list_services", "outcome": "ok"} for p in calls)
    finally:
        t.shutdown()


def test_tool_span_outcome_denied_and_records_authz_denial():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    try:
        async def call_next(_ctx):
            raise ToolError("Unknown tool: search_runbooks")

        with pytest.raises(ToolError):
            asyncio.run(mw.on_call_tool(_tool_context("search_runbooks"), call_next))

        span = t.span_exporter.get_finished_spans()[-1]
        assert span.attributes["gateway.outcome"] == "denied"

        points = _all_points(t.metric_reader.get_metrics_data())
        denials = [p for p in points if p.name == "gateway.authz.denials"]
        assert any(dict(p.attributes) == {"tool": "search_runbooks"} for p in denials)
    finally:
        t.shutdown()


def test_tool_span_outcome_denied_for_not_authorized_message():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    try:
        async def call_next(_ctx):
            raise ToolError("Not authorized.")

        with pytest.raises(ToolError):
            asyncio.run(mw.on_call_tool(_tool_context("service_details"), call_next))

        span = t.span_exporter.get_finished_spans()[-1]
        assert span.attributes["gateway.outcome"] == "denied"
    finally:
        t.shutdown()


def test_tool_span_outcome_error_for_other_exceptions():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    try:
        async def call_next(_ctx):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            asyncio.run(mw.on_call_tool(_tool_context("list_services"), call_next))

        span = t.span_exporter.get_finished_spans()[-1]
        assert span.attributes["gateway.outcome"] == "error"
    finally:
        t.shutdown()


def test_tool_span_outcome_error_for_generic_tool_error():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    try:
        async def call_next(_ctx):
            raise ToolError("query must not be empty")

        with pytest.raises(ToolError):
            asyncio.run(mw.on_call_tool(_tool_context("search_runbooks"), call_next))

        span = t.span_exporter.get_finished_spans()[-1]
        assert span.attributes["gateway.outcome"] == "error"
    finally:
        t.shutdown()


def test_tool_call_never_leaks_arguments_into_span_or_log():
    t = telemetry.configure_telemetry(exporter="memory")
    mw = telemetry.ToolSpanMiddleware(t)
    buf = _configure_captured_logging()
    sentinel_arg = "SENTINEL" + "-ARG-VALUE"
    try:
        async def call_next(_ctx):
            return "result"

        context = _tool_context("list_services", arguments={"status": sentinel_arg})
        asyncio.run(mw.on_call_tool(context, call_next))

        span = t.span_exporter.get_finished_spans()[-1]
        for value in span.attributes.values():
            assert sentinel_arg not in str(value)
        assert sentinel_arg not in buf.getvalue()
    finally:
        t.shutdown()


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def test_sample_ratio_zero_exports_no_spans_but_metrics_still_count():
    t = telemetry.configure_telemetry(exporter="memory", sample_ratio=0.0)
    try:
        with t.tracer.start_as_current_span("http.request"):
            pass
        assert len(t.span_exporter.get_finished_spans()) == 0

        t.record_http_request("GET", "/healthz", "2xx", 0.01)
        points = _all_points(t.metric_reader.get_metrics_data())
        assert any(p.name == "gateway.http.requests" for p in points)
    finally:
        t.shutdown()


# --------------------------------------------------------------------------
# Readiness gauge
# --------------------------------------------------------------------------


def test_gateway_ready_reflects_probe():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        points = _all_points(t.metric_reader.get_metrics_data())
        ready = next(p for p in points if p.name == "gateway.ready")
        assert ready.value == 0  # no probe set

        t.set_readiness_probe(lambda: {"sample_data": True, "signing_key": True})
        points = _all_points(t.metric_reader.get_metrics_data())
        ready = next(p for p in points if p.name == "gateway.ready")
        assert ready.value == 1

        t.set_readiness_probe(lambda: {"sample_data": True, "signing_key": False})
        points = _all_points(t.metric_reader.get_metrics_data())
        ready = next(p for p in points if p.name == "gateway.ready")
        assert ready.value == 0
    finally:
        t.shutdown()


def test_record_release_outcome_rejects_unknown_outcome():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        with pytest.raises(ValueError):
            t.record_release_outcome("sideways")
        t.record_release_outcome("rolled_back")
        points = _all_points(t.metric_reader.get_metrics_data())
        outcomes = [p for p in points if p.name == "gateway.release.outcomes"]
        assert any(dict(p.attributes) == {"outcome": "rolled_back"} for p in outcomes)
    finally:
        t.shutdown()


# --------------------------------------------------------------------------
# configure_from_env
# --------------------------------------------------------------------------


def test_configure_from_env_rejects_invalid_ratio():
    with pytest.raises(ValueError):
        telemetry.configure_from_env({"GATEWAY_TRACE_SAMPLE_RATIO": "5"})


def test_configure_from_env_rejects_non_numeric_ratio():
    with pytest.raises(ValueError):
        telemetry.configure_from_env({"GATEWAY_TRACE_SAMPLE_RATIO": "not-a-number"})


def test_configure_from_env_rejects_bad_interval():
    with pytest.raises(ValueError):
        telemetry.configure_from_env({"GATEWAY_METRICS_INTERVAL_SECONDS": "0"})


def test_configure_from_env_rejects_bad_exporter():
    with pytest.raises(ValueError):
        telemetry.configure_from_env({"GATEWAY_OTEL_EXPORTER": "carrier-pigeon"})


def test_configure_from_env_accepts_defaults():
    t = telemetry.configure_from_env({})
    try:
        assert isinstance(t, telemetry.Telemetry)
    finally:
        t.shutdown()


def test_configure_from_env_wires_release_id_into_resource():
    t = telemetry.configure_from_env(
        {"GATEWAY_OTEL_EXPORTER": "none", "GATEWAY_RELEASE_ID": "sha-abc123"}
    )
    try:
        resource_attrs = dict(t.tracer_provider.resource.attributes)
        assert resource_attrs["service.version"] == "sha-abc123"
    finally:
        t.shutdown()


# --------------------------------------------------------------------------
# stdout exporters
# --------------------------------------------------------------------------


def test_stdout_span_exporter_emits_parseable_redacted_json_lines():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        with t.tracer.start_as_current_span("http.request") as span:
            telemetry._set_attribute(span, "http.route", "/healthz")
            # simulate a value that would carry a secret shape if not redacted
            telemetry._set_attribute(span, "gateway.request_id", _kv("leaked", SENTINEL_GHO))
        spans = t.span_exporter.get_finished_spans()

        buf = io.StringIO()
        exporter = telemetry._StdoutSpanExporter(stream=buf)
        result = exporter.export(spans)
        assert result == telemetry.SpanExportResult.SUCCESS

        line = buf.getvalue().strip()
        assert SENTINEL_GHO not in line
        payload = json.loads(line)
        assert payload["otel"] == "span"
        assert payload["name"] == "http.request"
        assert "trace_id" in payload and "span_id" in payload
        assert payload["attributes"]["http.route"] == "/healthz"
    finally:
        t.shutdown()


def test_stdout_metric_exporter_emits_parseable_json_lines():
    t = telemetry.configure_telemetry(exporter="memory")
    try:
        t.record_http_request("GET", "/healthz", "2xx", 0.02)
        data = t.metric_reader.get_metrics_data()

        buf = io.StringIO()
        exporter = telemetry._StdoutMetricExporter(stream=buf)
        result = exporter.export(data)
        assert result == telemetry.MetricExportResult.SUCCESS

        line = buf.getvalue().strip()
        payload = json.loads(line)
        assert payload["otel"] == "metrics"
        assert any(point["name"] == "gateway.http.requests" for point in payload["points"])
    finally:
        t.shutdown()


# --------------------------------------------------------------------------
# current_request_id() / asgi_middleware()
# --------------------------------------------------------------------------


def test_current_request_id_none_outside_a_request():
    assert telemetry.current_request_id() is None


def test_asgi_middleware_returns_starlette_middleware_list():
    t = telemetry.configure_telemetry(exporter="none")
    try:
        middlewares = telemetry.asgi_middleware(t)
        assert len(middlewares) == 1
        assert middlewares[0].cls is telemetry.RequestContextMiddleware
    finally:
        t.shutdown()
