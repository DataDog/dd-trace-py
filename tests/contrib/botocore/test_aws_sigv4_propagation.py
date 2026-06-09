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
    that's owned by the API-call wrappers (patched_api_call in botocore,
    _wrapped_api_call in aiobotocore). The handler must inject cleanly
    without touching contextvar state, so we explicitly assert the
    contextvar is unchanged after the handler runs.
    """
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
        # touch it. The API-call wrappers (patched_api_call in botocore,
        # _wrapped_api_call in aiobotocore — not exercised in this unit
        # test) are the owners.
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


def test_client_created_before_patch_still_injects():
    """Regression: a client built BEFORE patch() must still get trace headers
    into the SigV4-signed request once patch() is active.

    botocore copies a Session's registered handlers onto a client at creation
    time, so a handler added to the Session after the client exists never
    reaches it. The before-sign handler is therefore registered on the
    client's own emitter at API-call time. Without that, a pre-patch client
    would be signed without trace headers while the urllib3 layer is still
    suppressed — silently dropping propagation (worse than before this fix,
    where urllib3 at least injected). This pins that the pre-patch client
    still gets propagation into SignedHeaders.
    """
    # Build the client BEFORE patching.
    session = botocore.session.Session()
    session.set_credentials(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    client = session.create_client("s3", region_name="us-east-1")

    patch()
    try:
        headers = _capture_signed_request(client)
    finally:
        unpatch()

    signed_headers = _parse_signed_headers(headers["Authorization"])
    assert "x-datadog-trace-id" in signed_headers, (
        f"pre-patch client did not get trace headers into SignedHeaders {signed_headers!r}"
    )
    assert "traceparent" in signed_headers, f"traceparent not in SignedHeaders {signed_headers!r}"


def test_handler_inert_after_unpatch():
    """Self-gate: after unpatch(), a client that registered the handler while
    patched must not inject on subsequent signing.

    The handler stays on the client's emitter for the client's lifetime
    (we don't unregister per-client on unpatch). It self-gates on patch state,
    so once unpatched it goes inert — otherwise it would inject pre-signing
    while a still-patched urllib3 layer injects post-signing, resurrecting the
    exact SigV4 mismatch this fix prevents.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.botocore.patch import patch

    patch()
    session = botocore.session.Session()
    session.set_credentials(access_key="A", secret_key="B")
    client = session.create_client("s3", region_name="us-east-1")
    # A traced call registers the handler on the client's emitter.
    _capture_signed_request(client)
    unpatch()

    # Emit before-sign directly on the client's (still-registered) emitter;
    # the handler must no-op because neither integration is patched.
    request = AWSRequest(method="GET", url="https://example.com/")
    with ddtrace.tracer.trace("test.span"):
        client.meta.events.emit(
            "before-sign.s3.GetObject",
            request=request,
            signing_name="s3",
            region_name="us-east-1",
            signature_version="v4",
            request_signer=None,
            operation_name="GetObject",
        )
    assert "x-datadog-trace-id" not in request.headers, (
        f"before-sign handler still injects after unpatch — self-gate failed. "
        f"Request headers after emit: {dict(request.headers)!r}"
    )


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


def test_presigned_url_with_sigv4_does_not_sign_datadog_headers(patched_botocore):
    """Regression: with Config(signature_version='s3v4'), botocore's
    `before-sign` event fires during presigned-URL generation with
    signature_version='s3v4-query'. If the handler injects propagation
    headers into the canonical request, the resulting URL's
    X-Amz-SignedHeaders parameter bakes those header names into the
    signature — and any subsequent caller of that URL must replay the
    exact same x-datadog-* / traceparent values or AWS rejects with
    SignatureDoesNotMatch. The handler must skip injection for the
    presign signing modes (signature_version ends in `-query` or
    contains `presign-post`).
    """
    from botocore.client import Config

    session = botocore.session.Session()
    session.set_credentials(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    client = session.create_client(
        "s3",
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )

    with ddtrace.tracer.trace("test.presign.sigv4"):
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": "b", "Key": "k"},
            ExpiresIn=60,
        )

    # Whole-URL guard: SigV4 query auth embeds the signed-headers list in
    # the URL via X-Amz-SignedHeaders=host%3B... — if x-datadog or
    # traceparent appears anywhere in the URL, the handler injected and
    # botocore baked it into the signature.
    lower_url = url.lower()
    assert "x-datadog" not in lower_url, f"datadog headers signed into presigned URL: {url}"
    assert "traceparent" not in lower_url, f"traceparent signed into presigned URL: {url}"

    # And explicit guard on the SignedHeaders clause, which is the exact
    # field that would have caused SignatureDoesNotMatch in production.
    match = re.search(r"X-Amz-SignedHeaders=([^&]+)", url, re.IGNORECASE)
    assert match is not None, f"presigned URL is missing X-Amz-SignedHeaders: {url}"
    signed_headers = match.group(1).replace("%3B", ";").lower().split(";")
    propagation_in_signed = [
        h for h in signed_headers if h in {"traceparent", "tracestate"} or h.startswith("x-datadog")
    ]
    assert propagation_in_signed == [], (
        f"propagation headers signed into presigned URL's X-Amz-SignedHeaders: {propagation_in_signed}"
    )


def test_unsigned_signature_version_does_not_crash_handler(patched_botocore):
    """Regression: with Config(signature_version=botocore.UNSIGNED) — the
    documented pattern for anonymous public-S3 access — botocore still
    emits `before-sign` (the UNSIGNED short-circuit happens AFTER the
    event). signature_version is then the botocore.UNSIGNED sentinel, a
    truthy non-string object with no .endswith. An earlier draft of the
    presign guard would call .endswith on it and raise AttributeError,
    which botocore.hooks._emit does not catch — the exception would
    propagate through RequestSigner.sign and crash the customer's AWS
    call. This test invokes the handler directly with UNSIGNED to pin
    that it does not crash.
    """
    import botocore as _botocore
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.botocore.patch import _inject_trace_headers_handler

    request = AWSRequest(method="GET", url="https://example.com/")

    with ddtrace.tracer.trace("test.unsigned"):
        # Should not raise — that's the whole assertion.
        _inject_trace_headers_handler(
            request=request,
            signing_name="s3",
            region_name="us-east-1",
            signature_version=_botocore.UNSIGNED,
            request_signer=None,
            operation_name="GetObject",
        )


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


def test_handler_does_not_overwrite_existing_propagation_headers(patched_botocore):
    """If the application has already set a propagation header (e.g., from
    a custom propagator), the handler must not overwrite it. This is the
    'never break user intent' guard. We also verify the handler still
    injects headers it did NOT preset, so a no-op handler can't pass this
    test.

    Uses the patched_botocore fixture because the handler self-gates on patch
    state and would no-op if the integration weren't active.
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


def test_ensure_before_sign_handler_returns_false_on_registration_failure():
    """_ensure_before_sign_handler returns False (and does not set the sentinel)
    when registering on the client emitter raises, so the caller knows the
    handler is not in place and must not suppress the urllib3 layer.
    """
    from ddtrace.contrib.internal.botocore.patch import _botocore_before_sign_handler
    from ddtrace.contrib.internal.botocore.patch import _ensure_before_sign_handler

    class _FailingEvents:
        def register(self, *args, **kwargs):
            raise RuntimeError("boom")

    class _FakeMeta:
        events = _FailingEvents()

    class _FakeClient:
        meta = _FakeMeta()

    client = _FakeClient()
    assert _ensure_before_sign_handler(client, _botocore_before_sign_handler) is False
    # Sentinel not set on failure, so a later call retries registration.
    assert getattr(client, "_dd_before_sign_registered", False) is False


def test_no_suppression_when_handler_registration_fails(s3_client):
    """Regression: if the before-sign handler cannot be registered, the API-call
    wrapper must NOT suppress the urllib3-layer injection — otherwise headers are
    injected at neither layer and propagation is silently dropped.

    We force registration to fail and snapshot the suppression contextvar at
    before-send (signing time). It must be False, so urllib3 still injects as a
    fallback.
    """
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    import ddtrace.contrib.internal.botocore.patch as bp

    seen = {}

    def before_send(request, **kwargs):
        seen["suppressed"] = _http_propagation_suppressed.get()
        raise _StopBeforeWire()

    s3_client.meta.events.register_first("before-send.s3.ListBuckets", before_send)

    original = bp._ensure_before_sign_handler
    bp._ensure_before_sign_handler = lambda client, handler: False
    try:
        with ddtrace.tracer.trace("test.list_buckets"):
            try:
                s3_client.list_buckets()
            except _StopBeforeWire:
                pass
    finally:
        bp._ensure_before_sign_handler = original

    assert seen.get("suppressed") is False, (
        "urllib3 injection was suppressed even though before-sign handler registration failed — "
        "propagation would be dropped at every layer"
    )


def test_distributed_tracing_disabled_suppresses_even_if_registration_fails(s3_client):
    """Opt-out edge: with DD_BOTOCORE_DISTRIBUTED_TRACING=false, urllib3 must be
    suppressed regardless of whether handler registration succeeds.

    The registration-failure fallback (don't suppress, let urllib3 inject) is
    correct only when distributed_tracing is ON. When it is OFF, suppression must
    stay on so no headers leak post-signing — otherwise dt=off + a failed
    registration would pass headers via urllib3, the exact opt-out leak.
    """
    from ddtrace import config
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    import ddtrace.contrib.internal.botocore.patch as bp

    seen = {}

    def before_send(request, **kwargs):
        seen["suppressed"] = _http_propagation_suppressed.get()
        raise _StopBeforeWire()

    s3_client.meta.events.register_first("before-send.s3.ListBuckets", before_send)

    original_reg = bp._ensure_before_sign_handler
    original_dt = config.botocore["distributed_tracing"]
    bp._ensure_before_sign_handler = lambda client, handler: False  # force registration failure
    config.botocore["distributed_tracing"] = False
    try:
        with ddtrace.tracer.trace("test.list_buckets"):
            try:
                s3_client.list_buckets()
            except _StopBeforeWire:
                pass
    finally:
        bp._ensure_before_sign_handler = original_reg
        config.botocore["distributed_tracing"] = original_dt

    assert seen.get("suppressed") is True, (
        "urllib3 injection was NOT suppressed with distributed_tracing off and registration failed — "
        "headers would leak post-signing despite the opt-out"
    )


def test_pin_disabled_does_not_set_suppression(s3_client):
    """Suppression-placement guard: `_http_propagation_suppressed` is set only
    on the full traced path, AFTER the pin.enabled() early-return.

    When the tracer is disabled, patched_api_call returns at `if not pin or
    not pin.enabled()` — before `_ensure_before_sign_handler` and before
    `_http_propagation_suppressed.set(True)`. So suppression must stay False:
    a subsequent non-AWS HTTP call in this task must still get header
    injection. This test fails if the suppression set is ever moved above the
    early-return (which would leak True with no try/finally reset).
    """
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    original_enabled = ddtrace.tracer.enabled
    ddtrace.tracer.enabled = False
    try:
        # Early-return path; the suppression contextvar must never be set.
        _capture_signed_request(s3_client)
        assert _http_propagation_suppressed.get() is False, (
            "_http_propagation_suppressed leaked True on the pin-disabled early-return path"
        )
    finally:
        ddtrace.tracer.enabled = original_enabled


def test_submodule_excluded_does_not_set_suppression(s3_client):
    """Suppression-placement guard for the _PATCHED_SUBMODULES early-return.

    When the endpoint is excluded, patched_api_call returns at the
    `_PATCHED_SUBMODULES and endpoint_name not in _PATCHED_SUBMODULES` branch —
    before `_ensure_before_sign_handler` and before the suppression set. So
    suppression must stay False. Fails if the suppression set is moved above
    this early-return.
    """
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.botocore.patch import _PATCHED_SUBMODULES

    # Exclude S3 so the call hits the submodule early-return. Save/restore in
    # case something else added entries before us.
    original_submodules = set(_PATCHED_SUBMODULES)
    _PATCHED_SUBMODULES.add("sqs")  # something other than s3
    try:
        _capture_signed_request(s3_client)
        assert _http_propagation_suppressed.get() is False, (
            "_http_propagation_suppressed leaked True on the _PATCHED_SUBMODULES early-return path"
        )
    finally:
        _PATCHED_SUBMODULES.clear()
        _PATCHED_SUBMODULES.update(original_submodules)
