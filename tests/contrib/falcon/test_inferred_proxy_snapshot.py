"""
Snapshot tests for inferred proxy spans (PR #18820, Azure Front Door support).

Inferred proxy spans are created by the core web-request handling whenever a
request carries `x-dd-proxy-*` headers, independent of the web framework. We
use an in-process Falcon test client purely as the vehicle to fire the request
through a real instrumented server code path — the same approach as
`tests/contrib/google_cloud_pubsub/test_pubsub_push.py` — so the snapshot
captures both the child `falcon.request` web span and the parent inferred
`azure.frontdoor` span with all of the tags that land on them.
"""

import falcon
import falcon.testing
import pytest

from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
from tests.utils import override_global_config


SNAPSHOT_IGNORES = [
    "meta._dd.tags.process",
    "meta._dd.span_links",
    "meta.tracestate",
    "meta.http.useragent",
]

# The error stack embeds file paths and line numbers that shift between runs and
# environments, so it is ignored in error-propagation snapshots.
ERROR_SNAPSHOT_IGNORES = SNAPSHOT_IGNORES + ["meta.error.stack"]

# Azure Front Door does not send a request-time header, so the tracer falls back
# to the current time for the inferred span start. "meta.http.url" and the span
# timings are deterministic; only the start time is not, but snapshot comparison
# ignores absolute timestamps.
AZURE_FRONTDOOR_HEADERS = {
    "x-dd-proxy": "azure-fd",
    # leading slash intentionally omitted to exercise path normalization
    "x-dd-proxy-path": "api/my-function",
    "x-dd-proxy-httpmethod": "GET",
    "x-dd-proxy-domain-name": "my-app.azurefd.net",
    "x-dd-proxy-resource-path": "/api/{resource}",
    "user-agent": "custom-client/1.0",
}


@pytest.fixture()
def falcon_inferred_proxy_client():
    class ProxiedResource:
        def on_get(self, req, resp):
            resp.status = falcon.HTTP_200
            resp.text = "OK"

    # The middleware is added explicitly rather than by calling the falcon integration's patch().
    # patch() wraps falcon.App.__init__ to prepend a TraceMiddleware, has no unpatch(), and so
    # leaks into every app built later in the session - double-instrumenting the apps that
    # tests/contrib/falcon/app/app.py builds with their own middleware. Passing the middleware
    # here exercises the same request path without mutating falcon globally.
    app = falcon.App(middleware=[TraceMiddleware()])
    app.add_route("/api/my-function", ProxiedResource())
    yield falcon.testing.TestClient(app)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_azure_frontdoor_creates_inferred_span(falcon_inferred_proxy_client):
    """A request with azure-fd proxy headers -> inferred azure.frontdoor span parenting the web span."""
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        resp = falcon_inferred_proxy_client.simulate_get("/api/my-function", headers=AZURE_FRONTDOOR_HEADERS)
        assert resp.status_code == 200


@pytest.fixture()
def falcon_inferred_proxy_error_client():
    class ErrorResource:
        def on_get(self, req, resp):
            raise Exception("handler failed")

    # see falcon_inferred_proxy_client on why patch() is not used here
    app = falcon.App(middleware=[TraceMiddleware()])
    app.add_route("/api/my-function", ErrorResource())
    yield falcon.testing.TestClient(app)


@pytest.mark.snapshot(ignores=ERROR_SNAPSHOT_IGNORES)
def test_azure_frontdoor_error_propagates_to_inferred_span(falcon_inferred_proxy_error_client):
    """When the handler raises, the 500 and error tags propagate from the web span up to the
    inferred azure.frontdoor span (both spans carry error=1).
    """
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        resp = falcon_inferred_proxy_error_client.simulate_get("/api/my-function", headers=AZURE_FRONTDOOR_HEADERS)
        assert resp.status_code == 500


def test_no_inferred_span_without_proxy_headers(falcon_inferred_proxy_client, test_spans):
    """A regular request (no x-dd-proxy headers) creates no inferred span even with the feature enabled."""
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        resp = falcon_inferred_proxy_client.simulate_get("/api/my-function")
        assert resp.status_code == 200

        spans = test_spans.pop()
        inferred = next((s for s in spans if s.name == "azure.frontdoor"), None)
        web = next((s for s in spans if s.name == "falcon.request"), None)

        assert inferred is None, "azure.frontdoor inferred span should not exist without proxy headers"
        assert web is not None, f"falcon.request span not found. Spans: {[s.name for s in spans]}"
