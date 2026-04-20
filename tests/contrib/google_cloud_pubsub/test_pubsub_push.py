"""
Tests for GCP Pub/Sub push subscription instrumentation.

Push subscriptions deliver messages via HTTP POST to a web server endpoint.
When configured with "payload unwrapping" + "write metadata", the HTTP request
body is raw message data, and Pub/Sub metadata/trace context is in HTTP headers.

This test simulates push delivery by sending HTTP requests with the expected
Pub/Sub headers to a Falcon test client with DDTrace Falcon instrumentation,
which exercises the same middleware code path as a real server — all
instrumentation hooks fire identically.
"""

import falcon
import falcon.testing
import pytest

from ddtrace.contrib.internal.falcon.patch import patch as falcon_patch
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer
from tests.utils import override_config
from tests.utils import override_global_config


PUSH_SUBSCRIPTION_NAME = "projects/test-project/subscriptions/test-push-subscription"
PUSH_SUBSCRIPTION_ID = "test-push-subscription"
PUSH_MESSAGE_ID = "12345"

SNAPSHOT_IGNORES = [
    "meta._dd.tags.process",
    "meta._dd.span_links",
    "meta.tracestate",
]


@pytest.fixture()
def pubsub_push_client():
    falcon_patch()

    class PushResource:
        def on_post(self, req, resp):
            resp.status = falcon.HTTP_200
            resp.text = "OK"

    app = falcon.App()
    app.add_route("/push", PushResource())
    yield falcon.testing.TestClient(app)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_push_subscription_creates_inferred_span(pubsub_push_client, test_spans):
    """Simulate a Pub/Sub push POST with headers -> inferred span with correct parentage."""
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        headers = {
            "X-Goog-Pubsub-Subscription-Name": PUSH_SUBSCRIPTION_NAME,
            "X-Goog-Pubsub-Message-Id": PUSH_MESSAGE_ID,
        }

        with tracer.trace("test.parent") as parent_span:
            HTTPPropagator.inject(parent_span.context, headers)

        resp = pubsub_push_client.simulate_post("/push", body=b"Hello push world", headers=headers)
        assert resp.status_code == 200

    receive_span = test_spans.find_span(name="gcp.pubsub.receive")
    assert not receive_span._get_links(), "No span links should be created when propagation_as_span_links is False"


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_push_subscription_propagation_as_span_links(pubsub_push_client, test_spans):
    """When propagation_as_span_links=True, receive span starts a new trace with a span link to the producer."""
    with override_config("google_cloud_pubsub", dict(propagation_as_span_links=True)):
        with override_global_config(dict(_inferred_proxy_services_enabled=True)):
            headers = {
                "X-Goog-Pubsub-Subscription-Name": PUSH_SUBSCRIPTION_NAME,
                "X-Goog-Pubsub-Message-Id": PUSH_MESSAGE_ID,
            }

            with tracer.trace("test.parent") as parent_span:
                HTTPPropagator.inject(parent_span.context, headers)

            resp = pubsub_push_client.simulate_post("/push", body=b"Hello push world", headers=headers)
            assert resp.status_code == 200

    receive_span = test_spans.find_span(name="gcp.pubsub.receive")
    assert len(receive_span._get_links()) == 1
    link = receive_span._get_links()[0]
    assert link.trace_id == parent_span.trace_id
    assert link.span_id == parent_span.span_id


@pytest.fixture()
def pubsub_push_error_client():
    falcon_patch()

    class ErrorPushResource:
        def on_post(self, req, resp):
            raise Exception("handler failed")

    app = falcon.App()
    app.add_route("/push", ErrorPushResource())
    yield falcon.testing.TestClient(app)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_push_subscription_error_propagates_to_inferred_span(pubsub_push_error_client):
    """When the handler raises, error tags propagate from the falcon span to the inferred gcp.pubsub.receive span."""
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        headers = {
            "X-Goog-Pubsub-Subscription-Name": PUSH_SUBSCRIPTION_NAME,
            "X-Goog-Pubsub-Message-Id": PUSH_MESSAGE_ID,
        }

        resp = pubsub_push_error_client.simulate_post("/push", body=b"Hello push world", headers=headers)
        assert resp.status_code == 500


def test_no_inferred_span_without_push_headers(pubsub_push_client, test_spans):
    """A regular POST (without Pub/Sub headers) creates no inferred span even with feature enabled."""
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        resp = pubsub_push_client.simulate_post("/push", body=b"normal body")
        assert resp.status_code == 200

        spans = test_spans.pop()

        receive_span = next((s for s in spans if s.name == "gcp.pubsub.receive"), None)
        falcon_span = next((s for s in spans if s.name == "falcon.request"), None)

        assert receive_span is None, "gcp.pubsub.receive span should not exist without push headers"
        assert falcon_span is not None, f"falcon.request span not found. Spans: {[s.name for s in spans]}"
