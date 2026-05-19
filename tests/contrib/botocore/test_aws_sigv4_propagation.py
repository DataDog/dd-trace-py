"""Tests that dd-trace-py injects trace propagation headers *before* botocore
signs the AWS request, so the headers are part of the SigV4 canonical request.

The bug: ddtrace was injecting trace headers in the urllib3 layer, which runs
AFTER botocore.signers.RequestSigner.sign(). Customers using SigV4-strict
endpoints (AppSync, API Gateway IAM auth, custom SigV4 servers) saw signature
mismatch errors. dd-trace-java avoids this by injecting at the
`before-sign` hook so headers are part of the signed canonical request.
"""

import re
from unittest import mock

import botocore.session
import pytest

import ddtrace
from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import unpatch


PROPAGATION_HEADERS = {
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "traceparent",
    "tracestate",
}


def _parse_signed_headers(authorization_header) -> set[str]:
    """Pull the SignedHeaders=... clause out of a SigV4 Authorization header.

    Authorization header format:
        AWS4-HMAC-SHA256 Credential=..., SignedHeaders=h1;h2;h3, Signature=...

    SignedHeaders is a semicolon-separated, lowercased list of every header
    AWS signed. If a header name is in SignedHeaders, it was part of the
    canonical request when the signature was computed.

    botocore may return the Authorization header value as bytes; decode it.
    """
    if isinstance(authorization_header, bytes):
        authorization_header = authorization_header.decode("ascii")
    match = re.search(r"SignedHeaders=([^,]+)", authorization_header)
    assert match is not None, f"no SignedHeaders clause in {authorization_header!r}"
    return {h.strip().lower() for h in match.group(1).split(";")}


@pytest.fixture
def patched_botocore():
    patch()
    yield
    unpatch()


@pytest.fixture
def s3_client(patched_botocore):
    """An S3 client with stubbed credentials. We never actually send anything;
    we hook before-send to capture the signed AWSRequest and short-circuit.
    """
    session = botocore.session.Session()
    session.set_credentials(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    client = session.create_client("s3", region_name="us-east-1")
    return client


def _capture_signed_request(client) -> dict:
    """Make a stubbed S3 ListBuckets call and return the AWSRequest at
    `before-send` (after signing). Aborts the actual network send by raising
    from the handler — pytest sees the exception and we use the captured
    request from the dict.
    """
    captured: dict = {}

    def before_send(request, **kwargs):
        captured["headers"] = request.headers
        # Abort the send. botocore re-raises so we catch ClientError-or-similar.
        raise _StopBeforeWire()

    client.meta.events.register_first("before-send.s3.ListBuckets", before_send)

    try:
        with ddtrace.tracer.trace("test.list_buckets"):
            client.list_buckets()
    except _StopBeforeWire:
        pass
    if "headers" not in captured:
        pytest.fail("before-send handler was never called — request did not reach the signing stage")
    return captured["headers"]


class _StopBeforeWire(Exception):
    pass


@pytest.fixture(autouse=True)
def _reset_http_propagation_suppressed():
    """Reset the _http_propagation_suppressed contextvar around every test.

    Tests in this file set the contextvar directly or call into
    patched_api_call, which sets it to True for the duration of an AWS
    call. Without this fixture, pytest-randomly can order tests such that
    a leaked True value causes test_subscriber_injects_when_propagation_not_suppressed
    (and similar) to take the early-return path in on_started and silently
    fail.

    patched_api_call's own Token reset handles its scope correctly, but
    tests that set the contextvar manually (or bypass patched_api_call
    entirely) still need a per-test reset, which this fixture provides.
    """
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    token = _http_propagation_suppressed.set(False)
    yield
    _http_propagation_suppressed.reset(token)


def test_trace_headers_are_in_sigv4_signed_headers(s3_client):
    """Proof-of-bug regression test.

    Expectation: when ddtrace is active and distributed_tracing is on,
    propagation headers must appear in the SigV4 SignedHeaders= clause —
    i.e. they were part of the canonical request when AWS signed it.

    Today this test fails because dd-trace-py injects in the urllib3 layer,
    which runs after RequestSigner.sign(). After this fix lands, the injection
    happens in a before-sign handler and SignedHeaders includes our headers.
    """
    headers = _capture_signed_request(s3_client)

    signed_headers = _parse_signed_headers(headers["Authorization"])

    # At minimum, the W3C and Datadog primary trace identifier must be signed.
    assert "x-datadog-trace-id" in signed_headers, (
        f"x-datadog-trace-id not in SignedHeaders {signed_headers!r}. "
        f"The header was injected AFTER signing — AWS will reject this request."
    )
    assert "traceparent" in signed_headers, f"traceparent not in SignedHeaders {signed_headers!r}"


def test_subscriber_skips_injection_when_propagation_suppressed():
    """When `_http_propagation_suppressed` is True, the shared HTTP subscriber
    must not call HTTPPropagator.inject. This is the seam botocore uses to
    avoid double-injection after its before-sign handler already injected.
    """
    from ddtrace._trace.subscribers.http_client import HttpClientTracingSubscriber
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    ctx = mock.MagicMock()
    event = mock.MagicMock()
    event.request_headers = {}
    event.integration_config = mock.MagicMock()
    event.integration_config.distributed_tracing_enabled = True
    ctx.event = event
    ctx.span.context = mock.MagicMock()

    token = _http_propagation_suppressed.set(True)
    try:
        with mock.patch("ddtrace._trace.subscribers.http_client.HTTPPropagator.inject") as inject:
            HttpClientTracingSubscriber.on_started(ctx)
            inject.assert_not_called()
    finally:
        _http_propagation_suppressed.reset(token)


def test_subscriber_injects_when_propagation_not_suppressed():
    """Default behavior must be preserved when the flag is unset."""
    from ddtrace._trace.subscribers.http_client import HttpClientTracingSubscriber

    ctx = mock.MagicMock()
    event = mock.MagicMock()
    event.request_headers = {}
    event.integration_config = mock.MagicMock()
    event.integration_config.distributed_tracing_enabled = True
    ctx.event = event
    ctx.span.context = mock.MagicMock()

    with (
        mock.patch("ddtrace._trace.subscribers.http_client.HTTPPropagator.inject") as inject,
        mock.patch(
            "ddtrace._trace.subscribers.http_client.trace_utils.distributed_tracing_enabled",
            return_value=True,
        ),
    ):
        HttpClientTracingSubscriber.on_started(ctx)
        inject.assert_called_once()


def test_before_sign_handler_injects_when_span_active(patched_botocore):
    """The handler must inject propagation headers into the AWSRequest.

    The handler is NOT responsible for the urllib3-suppression contextvar —
    that's owned exclusively by patched_api_call. The handler must inject
    cleanly without touching contextvar state, so we explicitly assert the
    contextvar is unchanged after the handler runs."""
    from botocore.awsrequest import AWSRequest

    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.botocore.patch import _inject_trace_headers_handler

    request = AWSRequest(method="GET", url="https://example.com/")
    assert "x-datadog-trace-id" not in request.headers

    # Reset the contextvar so the test starts from a known state.
    token = _http_propagation_suppressed.set(False)
    try:
        with ddtrace.tracer.trace("test.span"):
            _inject_trace_headers_handler(request=request)
        assert "x-datadog-trace-id" in request.headers
        # Contextvar must stay at the value we set — the handler must not
        # touch it. patched_api_call (not exercised in this unit test) is
        # the sole owner.
        assert _http_propagation_suppressed.get() is False
    finally:
        _http_propagation_suppressed.reset(token)


def test_before_sign_handler_noop_when_no_active_span(patched_botocore):
    """The handler must NOT inject when there's no active span — defensive
    against being called outside the normal flow.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.botocore.patch import _inject_trace_headers_handler

    request = AWSRequest(method="GET", url="https://example.com/")

    token = _http_propagation_suppressed.set(False)
    try:
        # No tracer.trace() context manager — no active span.
        _inject_trace_headers_handler(request=request)
        assert "x-datadog-trace-id" not in request.headers
        # Contextvar must remain False so urllib3 falls back to its own injection.
        assert _http_propagation_suppressed.get() is False
    finally:
        _http_propagation_suppressed.reset(token)


def test_before_sign_handler_noop_when_botocore_distributed_tracing_disabled(
    patched_botocore,
):
    """When DD_BOTOCORE_DISTRIBUTED_TRACING=false, the handler must not
    inject. (The contextvar guard in patched_api_call handles the urllib3
    side of suppression — covered separately.)
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace import config
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.botocore.patch import _inject_trace_headers_handler

    request = AWSRequest(method="GET", url="https://example.com/")

    original = config.botocore["distributed_tracing"]
    config.botocore["distributed_tracing"] = False
    token = _http_propagation_suppressed.set(False)
    try:
        with ddtrace.tracer.trace("test.span"):
            _inject_trace_headers_handler(request=request)
        assert "x-datadog-trace-id" not in request.headers
    finally:
        config.botocore["distributed_tracing"] = original
        _http_propagation_suppressed.reset(token)


def test_patch_registers_handler_on_new_sessions():
    """After patch() runs, emitting `before-sign` on any newly created Session
    must trigger our handler — proven by side effect (header injected into the
    request), not by poking botocore internals.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.botocore.patch import patch

    patch()
    try:
        session = botocore.session.Session()
        request = AWSRequest(method="GET", url="https://example.com/")
        with ddtrace.tracer.trace("test.span"):
            session.emit(
                "before-sign.s3.GetObject",
                request=request,
                signing_name="s3",
                region_name="us-east-1",
                signature_version="v4",
                request_signer=None,
                operation_name="GetObject",
            )
        assert "x-datadog-trace-id" in request.headers, (
            f"before-sign handler did not fire on new session — registered handlers may be missing. "
            f"Request headers after emit: {dict(request.headers)!r}"
        )
    finally:
        unpatch()


def test_unpatch_removes_session_init_wrapper():
    """After unpatch(), newly created Sessions must NOT have the handler —
    emitting `before-sign` must not mutate the request.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.botocore.patch import patch

    patch()
    unpatch()
    try:
        session = botocore.session.Session()
        request = AWSRequest(method="GET", url="https://example.com/")
        with ddtrace.tracer.trace("test.span"):
            session.emit(
                "before-sign.s3.GetObject",
                request=request,
                signing_name="s3",
                region_name="us-east-1",
                signature_version="v4",
                request_signer=None,
                operation_name="GetObject",
            )
        assert "x-datadog-trace-id" not in request.headers, (
            f"before-sign handler still fires after unpatch — Session.__init__ wrap was not removed. "
            f"Request headers after emit: {dict(request.headers)!r}"
        )
    finally:
        # Defensive: if any assertion or session call leaves state inconsistent, this resets it.
        # (Already unpatched at this point; this is harmless and symmetric with the other test.)
        unpatch()


def test_distributed_tracing_disabled_suppresses_urllib3_injection_for_aws(s3_client):
    """When DD_BOTOCORE_DISTRIBUTED_TRACING=false, AWS requests must contain
    NO trace propagation headers at any layer. Today's behavior leaks
    headers via the urllib3 subscriber even when botocore's flag is off —
    the contextvar guard in patched_api_call fixes that.
    """
    from ddtrace import config

    original = config.botocore["distributed_tracing"]
    config.botocore["distributed_tracing"] = False
    try:
        headers = _capture_signed_request(s3_client)
    finally:
        config.botocore["distributed_tracing"] = original

    leaked = {h for h in headers if h.lower() in PROPAGATION_HEADERS}
    assert leaked == set(), f"propagation headers leaked despite DD_BOTOCORE_DISTRIBUTED_TRACING=false: {leaked}"


@pytest.mark.parametrize("distributed_tracing", [True, False])
def test_contextvar_resets_after_botocore_call(s3_client, distributed_tracing):
    """A urllib3 request made AFTER a botocore call must still get headers
    injected — the suppression contextvar must not leak past the AWS call,
    regardless of which branch (True or False) initially set it inside
    patched_api_call.
    """
    from ddtrace import config
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    original = config.botocore["distributed_tracing"]
    config.botocore["distributed_tracing"] = distributed_tracing
    try:
        _capture_signed_request(s3_client)
    finally:
        config.botocore["distributed_tracing"] = original

    # Regardless of which branch was taken, the contextvar must be back to
    # False after the call returns.
    assert _http_propagation_suppressed.get() is False


def test_non_aws_urllib3_request_still_gets_headers(patched_botocore):
    """A urllib3 request made outside any botocore call must still have
    propagation headers injected. Regression guard: the new contextvar
    must not leak globally.
    """
    import urllib3

    # Patch urllib3 too so the integration is active.
    from ddtrace.contrib.internal.urllib3.patch import patch as patch_urllib3
    from ddtrace.contrib.internal.urllib3.patch import unpatch as unpatch_urllib3

    patch_urllib3()
    try:
        with mock.patch("ddtrace._trace.subscribers.http_client.HTTPPropagator.inject") as inject:
            # Use a connection that never connects — we just need the urlopen
            # call to reach the integration's wrapper. A bogus host raises
            # but the subscriber fires first.
            pool = urllib3.HTTPConnectionPool("127.0.0.1", port=1, maxsize=1)
            with ddtrace.tracer.trace("test.urllib3"):
                try:
                    pool.urlopen("GET", "/", retries=False, timeout=0.001)
                except Exception:
                    pass
            inject.assert_called_once()
    finally:
        unpatch_urllib3()


def test_presigned_url_generation_is_unaffected(s3_client):
    """generate_presigned_url does not go through _make_api_call. We must
    not crash, and the returned URL must not embed Datadog headers as
    query params (which would mean we accidentally injected in the wrong
    place).
    """
    with ddtrace.tracer.trace("test.presign"):
        url = s3_client.generate_presigned_url("get_object", Params={"Bucket": "b", "Key": "k"}, ExpiresIn=60)
    assert "x-datadog" not in url.lower()
    assert "traceparent" not in url.lower()


def test_concurrent_aws_calls_do_not_leak_suppression(patched_botocore):
    """Two AWS calls in two threads must each manage their own contextvar
    independently. We verify by snapshotting the contextvar inside the
    before-send hook for each call.
    """
    import threading

    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    results: dict[str, bool] = {}

    def run(name: str):
        session = botocore.session.Session()
        session.set_credentials(access_key="A", secret_key="B")
        client = session.create_client("s3", region_name="us-east-1")

        def before_send(request, **kwargs):
            results[name] = _http_propagation_suppressed.get()
            raise _StopBeforeWire()

        client.meta.events.register_first("before-send.s3.ListBuckets", before_send)
        try:
            with ddtrace.tracer.trace("test.span"):
                client.list_buckets()
        except _StopBeforeWire:
            pass

    threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Both calls must have seen suppression == True (the before-sign handler
    # set it before before-send fires).
    assert set(results) == {"a", "b"}, f"Not all threads completed; got keys: {set(results)}"
    assert results == {"a": True, "b": True}
    # And the parent test scope must NOT see suppression set.
    assert _http_propagation_suppressed.get() is False


def test_handler_does_not_overwrite_existing_propagation_headers():
    """If the application has already set a propagation header (e.g., from
    a custom propagator), the handler must not overwrite it. This is the
    'never break user intent' guard. We also verify the handler still
    injects headers it did NOT preset, so a no-op handler can't pass this
    test.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.botocore.patch import _inject_trace_headers_handler

    request = AWSRequest(method="GET", url="https://example.com/")
    request.headers["x-datadog-trace-id"] = "explicit-app-value"

    with ddtrace.tracer.trace("test.span"):
        _inject_trace_headers_handler(request=request)

    # The application's value is preserved.
    assert request.headers["x-datadog-trace-id"] == "explicit-app-value"
    # Headers the application did NOT pre-set were still injected,
    # proving the handler ran end-to-end rather than no-opping.
    assert "traceparent" in request.headers


def test_pin_disabled_does_not_leak_suppression_to_subsequent_calls(s3_client):
    """If the tracer is disabled at runtime, pin.enabled() returns False and
    patched_api_call early-returns before its try/finally. The before-sign
    handler can still fire — it must NOT leave the suppression contextvar set
    to True, because patched_api_call's try/finally never ran to reset it.

    Today this leaks: handler flips contextvar to True, no reset, and
    every subsequent non-AWS HTTP call in this task silently loses trace
    header injection."""
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    # Disable the tracer so pin.enabled() returns False and patched_api_call
    # early-returns before its try/finally.
    original_enabled = ddtrace.tracer.enabled
    ddtrace.tracer.enabled = False
    try:
        # Make a call. The early-return path runs; the handler still fires
        # at before-sign time because the Session wrap registered it.
        try:
            _capture_signed_request(s3_client)
        except _StopBeforeWire:
            # We intentionally short-circuit the wire send via _StopBeforeWire,
            # which propagates. Do not catch other exceptions — if
            # _capture_signed_request fails before reaching the signing stage
            # (e.g., from a broken monkey-patch), the assertion below would be
            # vacuously true and the test would be a false green.
            pass

        # The contextvar must be False after the call — the handler must
        # not leak True into the rest of this task.
        assert _http_propagation_suppressed.get() is False, (
            "before-sign handler leaked _http_propagation_suppressed=True "
            "outside patched_api_call's managed scope"
        )
    finally:
        ddtrace.tracer.enabled = original_enabled


def test_submodule_excluded_does_not_leak_suppression(s3_client):
    """If _PATCHED_SUBMODULES excludes the endpoint, patched_api_call early-returns
    before its try/finally, but the before-sign handler still fires. Same leak
    shape as the pin-disabled case."""
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.botocore.patch import _PATCHED_SUBMODULES

    # Exclude S3 from the patched submodules. patched_api_call hits the
    # `_PATCHED_SUBMODULES and endpoint_name not in _PATCHED_SUBMODULES` branch
    # and early-returns. Save/restore prior state in case something else
    # added entries before us.
    original_submodules = set(_PATCHED_SUBMODULES)
    _PATCHED_SUBMODULES.add("sqs")  # something other than s3
    try:
        try:
            _capture_signed_request(s3_client)
        except _StopBeforeWire:
            # We intentionally short-circuit the wire send via _StopBeforeWire,
            # which propagates. Do not catch other exceptions — if
            # _capture_signed_request fails before reaching the signing stage
            # (e.g., from a broken monkey-patch), the assertion below would be
            # vacuously true and the test would be a false green.
            pass

        assert _http_propagation_suppressed.get() is False, (
            "before-sign handler leaked _http_propagation_suppressed=True "
            "when patched_api_call early-returned via _PATCHED_SUBMODULES"
        )
    finally:
        _PATCHED_SUBMODULES.clear()
        _PATCHED_SUBMODULES.update(original_submodules)
