"""Tests that dd-trace-py injects trace propagation headers *before* botocore
signs the AWS request, so the headers are part of the SigV4 canonical request.

The bug: ddtrace was injecting trace headers in the urllib3 layer, which runs
AFTER botocore.signers.RequestSigner.sign(). Customers using SigV4-strict
endpoints (AppSync, API Gateway IAM auth, custom SigV4 servers) saw signature
mismatch errors. dd-trace-java avoids this by injecting at the
`before-sign` hook so headers are part of the signed canonical request.
"""

import re
from typing import Optional
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
    we hook before-send to capture the signed AWSRequest and short-circuit."""
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
    request from the dict."""
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
        pytest.fail(
            "before-send handler was never called — request did not reach the signing stage"
        )
    return captured["headers"]


class _StopBeforeWire(Exception):
    pass


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
    assert "traceparent" in signed_headers, (
        f"traceparent not in SignedHeaders {signed_headers!r}"
    )
