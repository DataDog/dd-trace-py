"""aiobotocore-specific SigV4 propagation tests.

The companion tests for the botocore integration live in
tests/contrib/botocore/test_aws_sigv4_propagation.py. The aiobotocore
suite venvs do not have boto3 installed, and the botocore conftest
imports modules that require boto3, so any aiobotocore test that
needs to be exercised in an aiobotocore venv must live here instead
of in the botocore tests directory.
"""

import re

import botocore.client
import botocore.session
import pytest

import ddtrace


class _StopBeforeWire(Exception):
    pass


def _parse_signed_headers(authorization_header) -> set:
    """Pull the lowercased SignedHeaders=... list out of a SigV4 Authorization
    header (``AWS4-HMAC-SHA256 Credential=..., SignedHeaders=h1;h2, Signature=...``).
    """
    if isinstance(authorization_header, bytes):
        authorization_header = authorization_header.decode("ascii")
    match = re.search(r"SignedHeaders=([^,]+)", authorization_header)
    assert match is not None, f"no SignedHeaders clause in {authorization_header!r}"
    return {h.strip().lower() for h in match.group(1).split(";")}


def _capture_aio_signed_request(session) -> dict:
    """Create an aiobotocore S3 client from ``session``, make a stubbed
    ListBuckets call, and return the signed request headers captured at
    ``before-send`` (after signing). The wire send is aborted via
    _StopBeforeWire so no network is needed.

    aiobotocore's ``create_client`` is async (an async context manager), so
    the client is created inside the event loop here.
    """
    import asyncio

    captured: dict = {}

    def before_send(request, **kwargs):
        captured["headers"] = request.headers
        raise _StopBeforeWire()

    async def run():
        async with session.create_client("s3", region_name="us-east-1") as client:
            client.meta.events.register_first("before-send.s3.ListBuckets", before_send)
            try:
                await client.list_buckets()
            except _StopBeforeWire:
                pass

    asyncio.run(run())
    if "headers" not in captured:
        pytest.fail("before-send handler was never called — request did not reach the signing stage")
    return captured["headers"]


@pytest.fixture(autouse=True)
def _reset_http_propagation_suppressed():
    """Reset the _http_propagation_suppressed contextvar around every test.

    Mirrors the botocore-side fixture. Without this, pytest-randomly can
    order tests such that a leaked True value silently turns the
    HttpClientTracingSubscriber's on_started into a no-op.
    """
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed

    token = _http_propagation_suppressed.set(False)
    yield
    _http_propagation_suppressed.reset(token)


def test_aiobotocore_only_patch_injects_into_signed_request():
    """A user who enables ONLY the aiobotocore integration (not botocore) must
    still get trace headers into the SigV4-signed request. The handler is
    registered on the client's own emitter at API-call time by
    _ensure_before_sign_handler, so aiobotocore-only setups are covered.
    """
    import aiobotocore.session

    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore

    # IMPORTANT: do NOT call botocore's patch() — aiobotocore alone must suffice.
    patch_aiobotocore()
    try:
        session = aiobotocore.session.AioSession()
        session.set_credentials(
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        headers = _capture_aio_signed_request(session)
    finally:
        unpatch_aiobotocore()

    signed_headers = _parse_signed_headers(headers["Authorization"])
    assert "x-datadog-trace-id" in signed_headers, (
        f"aiobotocore-only patch did not get trace headers into SignedHeaders {signed_headers!r}"
    )
    assert "traceparent" in signed_headers, f"traceparent not in SignedHeaders {signed_headers!r}"


def test_aiobotocore_client_created_before_patch_still_injects():
    """Regression: an aiobotocore client built BEFORE patch() must still get
    trace headers into the signed request once patch() is active — the
    before-sign handler is registered on the client emitter at call time, so
    it does not depend on the client's Session being created after patch().

    aiobotocore's create_client is async, so the client is created (awaited)
    before patch() runs, then the call is made after patch() — all within one
    event loop. patch() wraps AioBaseClient._make_api_call at the class level,
    so the pre-created client's call still routes through _wrapped_api_call.
    """
    import asyncio

    import aiobotocore.session

    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore

    captured: dict = {}

    def before_send(request, **kwargs):
        captured["headers"] = request.headers
        raise _StopBeforeWire()

    async def run():
        session = aiobotocore.session.AioSession()
        session.set_credentials(
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        # Create the client BEFORE patching. create_client returns an async
        # context manager (ClientCreatorContext); enter it manually so the
        # client exists before patch() runs and is cleaned up after.
        client_ctx = session.create_client("s3", region_name="us-east-1")
        client = await client_ctx.__aenter__()
        try:
            patch_aiobotocore()
            client.meta.events.register_first("before-send.s3.ListBuckets", before_send)
            try:
                await client.list_buckets()
            except _StopBeforeWire:
                pass
        finally:
            unpatch_aiobotocore()
            await client_ctx.__aexit__(None, None, None)

    asyncio.run(run())

    assert "headers" in captured, "request never reached the signing stage"
    signed_headers = _parse_signed_headers(captured["headers"]["Authorization"])
    assert "x-datadog-trace-id" in signed_headers, (
        f"pre-patch aiobotocore client did not get trace headers into SignedHeaders {signed_headers!r}"
    )


def test_aiobotocore_wrapped_api_call_suppresses_propagation_during_await():
    """aiobotocore transports through aiohttp.ClientSession; if the aiohttp
    integration is also patched (the normal auto-instrumentation case), its
    HttpClientTracingSubscriber would inject propagation headers AFTER SigV4
    has signed the request, breaking the signature. _wrapped_api_call must
    set _http_propagation_suppressed=True around the await so the subscriber
    bails. We snapshot the contextvar from inside the awaited function —
    that's the exact moment the aiohttp event would fire in production.
    """
    import asyncio

    from ddtrace._trace.pin import Pin
    from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
    from ddtrace.contrib.internal.aiobotocore.patch import _wrapped_api_call

    inside: dict[str, bool] = {}

    class _FakeEndpoint:
        _endpoint_prefix = "s3"

    class _FakeMeta:
        region_name = "us-east-1"

    class _FakeClient:
        _endpoint = _FakeEndpoint()
        meta = _FakeMeta()

    instance = _FakeClient()
    Pin().onto(instance)

    async def fake_make_api_call(*args, **kwargs):
        inside["suppressed"] = _http_propagation_suppressed.get()
        return {
            "Body": None,
            "ResponseMetadata": {
                "HTTPHeaders": {},
                "HTTPStatusCode": 200,
                "RetryAttempts": 0,
            },
        }

    asyncio.run(_wrapped_api_call(fake_make_api_call, instance, ("ListBuckets", {}), {}))

    assert inside.get("suppressed") is True, (
        "_wrapped_api_call did not suppress _http_propagation_suppressed during the await; "
        "aiohttp's HttpClientTracingSubscriber would inject headers post-signing"
    )
    # And the contextvar must reset after the call.
    assert _http_propagation_suppressed.get() is False


@pytest.mark.parametrize("unpatch_botocore_first", [True, False])
def test_dual_patch_unpatch_cleanly_unwraps_make_api_call(unpatch_botocore_first):
    """With both integrations patched and then unpatched (in either order),
    neither ``_make_api_call`` wrap may be left behind.

    Option A registers the before-sign handler per-client at call time, so the
    only class-level state is the ``_make_api_call`` wrap on each integration's
    client class — independent wraps on different classes, with no shared
    Session.__init__ machinery. This pins that each integration unwraps its own
    class cleanly regardless of order.
    """
    import aiobotocore.client

    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore
    from ddtrace.contrib.internal.botocore.patch import patch as patch_botocore
    from ddtrace.contrib.internal.botocore.patch import unpatch as unpatch_botocore

    patch_botocore()
    patch_aiobotocore()
    try:
        assert hasattr(botocore.client.BaseClient._make_api_call, "__wrapped__")
        assert hasattr(aiobotocore.client.AioBaseClient._make_api_call, "__wrapped__")
    finally:
        if unpatch_botocore_first:
            unpatch_botocore()
            unpatch_aiobotocore()
        else:
            unpatch_aiobotocore()
            unpatch_botocore()

    assert not hasattr(botocore.client.BaseClient._make_api_call, "__wrapped__"), (
        "botocore BaseClient._make_api_call still wrapped after unpatching both integrations"
    )
    assert not hasattr(aiobotocore.client.AioBaseClient._make_api_call, "__wrapped__"), (
        "aiobotocore AioBaseClient._make_api_call still wrapped after unpatching both integrations"
    )


def _emit_before_sign(client):
    """Emit a before-sign event on a client's emitter with an active span and
    return the AWSRequest, so the test can inspect whether the registered
    handler injected. Used to probe handler activity without a full call.
    """
    from botocore.awsrequest import AWSRequest

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
    return request


def test_botocore_client_handler_inert_when_only_aiobotocore_remains_patched():
    """Asymmetric unpatch: a botocore client's before-sign handler must go
    inert once BOTOCORE is unpatched, even though aiobotocore stays patched.

    If it stayed active, it would inject pre-signing while botocore's
    _make_api_call (now unwrapped) no longer suppresses the urllib3 layer —
    which injects post-signing, resurrecting the SigV4 mismatch. The per-owner
    gate (_botocore_before_sign_handler gates on botocore's own patch state,
    not "either integration is patched") is what prevents this.
    """
    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore
    from ddtrace.contrib.internal.botocore.patch import _botocore_before_sign_handler
    from ddtrace.contrib.internal.botocore.patch import _ensure_before_sign_handler
    from ddtrace.contrib.internal.botocore.patch import patch as patch_botocore
    from ddtrace.contrib.internal.botocore.patch import unpatch as unpatch_botocore

    patch_botocore()
    patch_aiobotocore()
    try:
        session = botocore.session.Session()
        session.set_credentials(access_key="A", secret_key="B")
        client = session.create_client("s3", region_name="us-east-1")
        # Register the handler on the botocore client, as a traced call would.
        _ensure_before_sign_handler(client, _botocore_before_sign_handler)
        # Unpatch ONLY botocore; aiobotocore stays patched.
        unpatch_botocore()

        request = _emit_before_sign(client)
        assert "x-datadog-trace-id" not in request.headers, (
            "botocore client handler still injected after botocore was unpatched "
            "(aiobotocore still patched) — asymmetric-unpatch SigV4 regression"
        )
    finally:
        unpatch_aiobotocore()
        unpatch_botocore()


def test_aiobotocore_client_handler_inert_when_only_botocore_remains_patched():
    """Reverse asymmetric unpatch: an aiobotocore client's handler must go
    inert once AIOBOTOCORE is unpatched, even though botocore stays patched.
    """
    import asyncio

    import aiobotocore.session

    from ddtrace.contrib.internal.aiobotocore.patch import _aiobotocore_before_sign_handler
    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore
    from ddtrace.contrib.internal.botocore.patch import _ensure_before_sign_handler
    from ddtrace.contrib.internal.botocore.patch import patch as patch_botocore
    from ddtrace.contrib.internal.botocore.patch import unpatch as unpatch_botocore

    result: dict = {}

    async def run():
        session = aiobotocore.session.AioSession()
        session.set_credentials(access_key="A", secret_key="B")
        client_ctx = session.create_client("s3", region_name="us-east-1")
        client = await client_ctx.__aenter__()
        try:
            patch_botocore()
            patch_aiobotocore()
            # Register the aiobotocore client's owner-gated handler.
            _ensure_before_sign_handler(client, _aiobotocore_before_sign_handler)
            # Unpatch ONLY aiobotocore; botocore stays patched.
            unpatch_aiobotocore()
            result["request"] = _emit_before_sign(client)
        finally:
            unpatch_botocore()
            unpatch_aiobotocore()
            await client_ctx.__aexit__(None, None, None)

    asyncio.run(run())

    assert "x-datadog-trace-id" not in result["request"].headers, (
        "aiobotocore client handler still injected after aiobotocore was unpatched "
        "(botocore still patched) — asymmetric-unpatch SigV4 regression"
    )
