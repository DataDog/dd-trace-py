import django
import pytest

from tests.utils import assert_span_http_status_code


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
