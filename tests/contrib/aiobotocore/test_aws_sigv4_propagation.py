"""aiobotocore-specific SigV4 propagation tests.

The companion tests for the botocore integration live in
tests/contrib/botocore/test_aws_sigv4_propagation.py. The aiobotocore
suite venvs do not have boto3 installed, and the botocore conftest
imports modules that require boto3, so any aiobotocore test that
needs to be exercised in an aiobotocore venv must live here instead
of in the botocore tests directory.
"""

import botocore.session
import pytest

import ddtrace


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


def test_aiobotocore_patch_also_registers_before_sign_handler():
    """aiobotocore.AioSession subclasses botocore.session.Session and calls
    super().__init__(). The botocore integration's patch() wraps
    Session.__init__ — but a user who only enables the aiobotocore
    integration (not the botocore integration) never sees that wrap. This
    test pins that aiobotocore's patch() installs the same wrap so the
    SigV4 fix applies to aiobotocore-only setups too.
    """
    from botocore.awsrequest import AWSRequest

    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore

    # IMPORTANT: do NOT call botocore's patch() — we're verifying that
    # aiobotocore's patch() alone is sufficient.
    patch_aiobotocore()
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
            "aiobotocore.patch() did not install the before-sign handler — aiobotocore-only users get no SigV4 fix"
        )
    finally:
        unpatch_aiobotocore()


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


def test_session_init_fully_unwrapped_after_dual_unpatch():
    """When both botocore and aiobotocore are patched, only ONE wrapt layer
    must be installed on Session.__init__ (single shared wrap via
    _patch_session_init). After both unpatch() calls run, Session.__init__
    must be fully restored to the original in either unpatch order.

    Regression: prior to the shared-helper refactor, each integration
    wrapped independently and the last unpatch() removed only one layer,
    leaving a stale wrapper installed indefinitely.
    """
    from ddtrace.contrib.internal.aiobotocore.patch import patch as patch_aiobotocore
    from ddtrace.contrib.internal.aiobotocore.patch import unpatch as unpatch_aiobotocore
    from ddtrace.contrib.internal.botocore.patch import patch as patch_botocore
    from ddtrace.contrib.internal.botocore.patch import unpatch as unpatch_botocore

    original_init = botocore.session.Session.__init__

    def _layers(fn) -> int:
        count = 0
        while hasattr(fn, "__wrapped__"):
            count += 1
            fn = fn.__wrapped__
            if count > 5:  # safety guard
                break
        return count

    # Order 1: unpatch botocore first, then aiobotocore.
    patch_botocore()
    patch_aiobotocore()
    try:
        assert _layers(botocore.session.Session.__init__) == 1, (
            f"Session.__init__ has {_layers(botocore.session.Session.__init__)} wrapt layers stacked "
            f"after patching both integrations, expected 1 (single shared wrap)"
        )
    finally:
        unpatch_botocore()
        unpatch_aiobotocore()

    assert botocore.session.Session.__init__ is original_init, (
        "Session.__init__ still wrapped after unpatching both integrations "
        "(order: botocore -> aiobotocore); a stale wrapt layer remains installed"
    )

    # Order 2: unpatch aiobotocore first, then botocore.
    patch_botocore()
    patch_aiobotocore()
    try:
        pass
    finally:
        unpatch_aiobotocore()
        unpatch_botocore()

    assert botocore.session.Session.__init__ is original_init, (
        "Session.__init__ still wrapped after unpatching both integrations "
        "(order: aiobotocore -> botocore); a stale wrapt layer remains installed"
    )
