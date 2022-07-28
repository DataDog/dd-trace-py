import django
import pytest

from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.django_db
def test_trace_exceptions(client, test_spans):  # noqa flake8 complains about shadowing test_spans
    response = client.get("/users/")

    # Our custom exception handler is setting the status code to 500
    assert response.status_code == 500

    sp = test_spans.get_root_span()
    assert sp.name == "django.request"
    if django.VERSION >= (2, 2, 0):
        assert sp.resource == "GET ^users/$"
    else:
        assert sp.resource == "GET tests.contrib.djangorestframework.app.views.UserViewSet"
    assert sp.error == 1
    assert sp.span_type == "web"
    assert_span_http_status_code(sp, 500)
    assert sp.get_tag("http.method") == "GET"

    # the DRF integration should set the traceback on the django.view.dispatch span
    # (as it's the current span when the exception info is set)
    view_dispatch_spans = list(test_spans.filter_spans(name="django.view.dispatch"))
    assert len(view_dispatch_spans) == 1
    err_span = view_dispatch_spans[0]
    assert err_span.error == 1
    assert err_span.get_tag("error.msg") == "Authentication credentials were not provided."
    assert "NotAuthenticated" in err_span.get_tag("error.stack")


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        client.post("/users/", payload, content_type="application/x-www-form-urlencoded")
        root_span = test_spans.spans[0]
        query = dict(_context.get_item("http.request.body", span=root_span))

        assert root_span.get_tag("_dd.appsec.json") is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}
