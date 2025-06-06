# -*- coding: utf-8 -*-
import itertools
import os
import subprocess

import django
from django.core.signals import request_started
from django.core.wsgi import get_wsgi_application
from django.db import close_old_connections
from django.db import connections
from django.test import modify_settings
from django.test import override_settings
from django.test.client import RequestFactory
from django.utils.functional import SimpleLazyObject
from django.views.generic import TemplateView
import mock
import pytest
import wrapt

from ddtrace import config
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import USER_KEEP
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.django.patch import instrument_view
from ddtrace.contrib.internal.django.patch import traced_get_response
from ddtrace.contrib.internal.django.utils import get_request_uri
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.schema import schematize_service_name
from ddtrace.propagation._utils import get_wsgi_header
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.opentracer.utils import init_tracer
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_dict_issuperset
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config
from tests.utils import override_http_config


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def cleanup_connections():
    yield
    connections.close_all()


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_django_v2XX_request_root_span(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.request` root span
    """
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    spans = test_spans.get_spans()
    # Assert the correct number of traces and spans
    assert len(spans) == 26

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()

    if django.VERSION >= (2, 2, 0):
        resource = "GET ^$"
    else:
        resource = "GET tests.contrib.django.views.index"

    meta = {
        "component": "django",
        "django.request.class": "django.core.handlers.wsgi.WSGIRequest",
        "django.response.class": "django.http.response.HttpResponse",
        "django.user.is_authenticated": "False",
        "django.view": "tests.contrib.django.views.index",
        "http.method": "GET",
        "http.status_code": "200",
        "http.url": "http://testserver/",
    }
    if django.VERSION >= (2, 2, 0):
        meta["http.route"] = "^$"

    assert http.QUERY_STRING not in root.get_tags()
    root.assert_matches(
        name="django.request",
        service="django",
        resource=resource,
        parent_id=None,
        span_type="web",
        error=0,
        meta=meta,
    )


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_django_v2XX_alter_root_resource(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.request` root span
    """
    resp = client.get("/alter-resource/")
    assert resp.status_code == 200
    assert resp.content == b""

    spans = test_spans.get_spans()
    # Assert the correct number of traces and spans
    assert len(spans) == 26

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()

    meta = {
        "component": "django",
        "django.request.class": "django.core.handlers.wsgi.WSGIRequest",
        "django.response.class": "django.http.response.HttpResponse",
        "django.user.is_authenticated": "False",
        "django.view": "tests.contrib.django.views.alter_resource",
        "http.method": "GET",
        "http.status_code": "200",
        "http.url": "http://testserver/alter-resource/",
    }
    if django.VERSION >= (2, 2, 0):
        meta["http.route"] = "^alter-resource/$"

    assert http.QUERY_STRING not in root.get_tags()
    root.assert_matches(
        name="django.request",
        service="django",
        resource="custom django.request resource",
        parent_id=None,
        span_type="web",
        error=0,
        meta=meta,
    )


@pytest.mark.skipif(django.VERSION >= (2, 0, 0), reason="")
def test_v1XX_middleware(client, test_spans):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    # Assert the correct number of traces and spans
    if django.VERSION < (1, 11, 0):
        test_spans.assert_span_count(15)
    else:
        test_spans.assert_span_count(16)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name="django.middleware"))
    if django.VERSION < (1, 11, 0):
        assert len(middleware_spans) == 13
    else:
        assert len(middleware_spans) == 14

    root = test_spans.get_root_span()
    root.assert_matches(name="django.request")

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name="django.middleware",
            service="django",
            error=0,
            span_type=None,
            parent_id=root.span_id,  # They are all children of the root django.request
        )

    # DEV: Order matters here, we want all `process_request` before `process_view`, before `process_response`
    expected_resources = [
        "django.contrib.sessions.middleware.SessionMiddleware.process_request",
        "django.middleware.common.CommonMiddleware.process_request",
        "django.middleware.csrf.CsrfViewMiddleware.process_request",  # Not in < 1.11.0
        "django.contrib.auth.middleware.AuthenticationMiddleware.process_request",
        "django.contrib.auth.middleware.SessionAuthenticationMiddleware.process_request",
        "django.contrib.messages.middleware.MessageMiddleware.process_request",
        "django.middleware.security.SecurityMiddleware.process_request",
        "django.middleware.csrf.CsrfViewMiddleware.process_view",
        "django.middleware.security.SecurityMiddleware.process_response",
        "django.middleware.clickjacking.XFrameOptionsMiddleware.process_response",
        "django.contrib.messages.middleware.MessageMiddleware.process_response",
        "django.middleware.csrf.CsrfViewMiddleware.process_response",
        "django.middleware.common.CommonMiddleware.process_response",
        "django.contrib.sessions.middleware.SessionMiddleware.process_response",
    ]
    if django.VERSION < (1, 11, 0):
        expected_resources.remove("django.middleware.csrf.CsrfViewMiddleware.process_request")
    middleware_spans = sorted(middleware_spans, key=lambda s: s.start)
    span_resources = [s.resource for s in middleware_spans]
    assert span_resources == expected_resources


def test_disallowed_host(client, test_spans):
    with override_settings(ALLOWED_HOSTS="not-testserver"):
        resp = client.get("/")
        assert resp.status_code == 400
        assert b"Bad Request (400)" in resp.content

    root_span = test_spans.get_root_span()
    assert root_span.get_tag("http.status_code") == "400"
    assert root_span.get_tag("http.url") == "http://testserver/"


def test_http_header_tracing_disabled(client, test_spans):
    with override_config("django", {}):
        config.django.http._reset()
        headers = {
            get_wsgi_header("my-header"): "my_value",
        }
        resp = client.get("/", **headers)

    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    root = test_spans.get_root_span()

    assert root.get_tag("http.request.headers.my-header") is None
    assert root.get_tag("http.response.headers.my-response-header") is None


def test_http_header_tracing_enabled(client, test_spans):
    with override_config("django", {}):
        config.django.http.trace_headers(["my-header", "my-response-header"])

        headers = {
            get_wsgi_header("my-header"): "my_value",
        }
        resp = client.get("/", **headers)

    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    root = test_spans.get_root_span()

    assert root.get_tag("http.request.headers.my-header") == "my_value"
    assert root.get_tag("http.response.headers.my-response-header") == "my_response_value"


"""
Middleware tests
"""


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_v2XX_middleware(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    # Assert the correct number of traces and spans
    test_spans.assert_span_count(26)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name="django.middleware"))
    assert len(middleware_spans) == 24

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name="django.middleware",
            service="django",
            error=0,
            span_type=None,
        )

    span_resources = {
        "django.contrib.auth.middleware.AuthenticationMiddleware.__call__",
        "django.contrib.auth.middleware.AuthenticationMiddleware.process_request",
        "django.contrib.messages.middleware.MessageMiddleware.__call__",
        "django.contrib.messages.middleware.MessageMiddleware.process_request",
        "django.contrib.messages.middleware.MessageMiddleware.process_response",
        "django.contrib.sessions.middleware.SessionMiddleware.__call__",
        "django.contrib.sessions.middleware.SessionMiddleware.process_request",
        "django.contrib.sessions.middleware.SessionMiddleware.process_response",
        "django.middleware.clickjacking.XFrameOptionsMiddleware.__call__",
        "django.middleware.clickjacking.XFrameOptionsMiddleware.process_response",
        "django.middleware.common.CommonMiddleware.__call__",
        "django.middleware.common.CommonMiddleware.process_request",
        "django.middleware.common.CommonMiddleware.process_response",
        "django.middleware.csrf.CsrfViewMiddleware.__call__",
        "django.middleware.csrf.CsrfViewMiddleware.process_request",
        "django.middleware.csrf.CsrfViewMiddleware.process_response",
        "django.middleware.csrf.CsrfViewMiddleware.process_view",
        "django.middleware.security.SecurityMiddleware.__call__",
        "django.middleware.security.SecurityMiddleware.process_request",
        "django.middleware.security.SecurityMiddleware.process_response",
        "tests.contrib.django.middleware.ClsMiddleware.__call__",
        "tests.contrib.django.middleware.fn_middleware",
        "tests.contrib.django.middleware.EverythingMiddleware.__call__",
        "tests.contrib.django.middleware.EverythingMiddleware.process_view",
    }
    assert set([s.resource for s in middleware_spans]) == span_resources

    # Get middleware spans in reverse order of start time
    middleware_spans = sorted(middleware_spans, key=lambda s: s.start, reverse=True)

    # Assert the first middleware span's parent is the root span (django.request)
    root_span = test_spans.get_root_span()
    assert root_span.name == "django.request"
    first_middleware = middleware_spans[-1]
    assert first_middleware.parent_id == root_span.span_id


def test_django_request_not_found(client, test_spans):
    """
    When making a request to a Django app
        When the endpoint doesn't exist
            We create a 404 span
    """
    resp = client.get("/unknown/endpoint")
    assert resp.status_code == 404

    if django.VERSION >= (3, 0, 0):
        content = (
            b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Not Found</title>\n'
            b"</head>\n<body>\n  <h1>Not Found</h1><p>The requested resource was not found "
            b"on this server.</p>\n</body>\n</html>\n"
        )
    elif django.VERSION >= (1, 11, 0):
        content = b"<h1>Not Found</h1><p>The requested resource was not found on this server.</p>"
    else:
        content = b"<h1>Not Found</h1><p>The requested URL /unknown/endpoint was not found on this server.</p>"
    assert resp.content == content

    # Assert the correct number of traces and spans
    if django.VERSION >= (2, 0, 0):
        span_count = 27
    elif django.VERSION >= (1, 11, 0):
        span_count = 18
    else:
        span_count = 16
    test_spans.assert_span_count(span_count)

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()
    root.assert_matches(
        name="django.request",
        service="django",
        resource="GET 404",
        parent_id=None,
        span_type="web",
        error=0,
        meta={
            "django.request.class": "django.core.handlers.wsgi.WSGIRequest",
            "django.response.class": "django.http.response.HttpResponseNotFound",
            "http.method": "GET",
            "http.status_code": "404",
            "http.url": "http://testserver/unknown/endpoint",
        },
    )

    # Assert template render
    render_spans = list(test_spans.filter_spans(name="django.template.render"))
    assert len(render_spans) == 1

    render_span = render_spans[0]
    render_span.assert_matches(
        name="django.template.render",
        resource="django.template.base.Template.render",
        meta={
            "django.template.engine.class": "django.template.engine.Engine",
        },
    )


def test_middleware_trace_error_500(client, test_spans):
    # ensures exceptions generated by views are traced
    with modify_settings(
        **(
            dict(MIDDLEWARE={"append": "tests.contrib.django.middleware.CatchExceptionMiddleware"})
            if django.VERSION >= (2, 0, 0)
            else dict(MIDDLEWARE_CLASSES={"append": "tests.contrib.django.middleware.CatchExceptionMiddleware"})
        )
    ):
        assert client.get("/error-500/").status_code == 500

    error_spans = list(test_spans.filter_spans(error=1))
    # There should be 3 spans flagged as errors
    # 1. The view span which wraps the original error
    # 2. The root span which should just be flagged as an error (no exception info)
    # 3. The middleware span that catches the exception and converts it to a 500
    assert len(error_spans) == 3

    # Test the root span
    span = test_spans.get_root_span()
    assert span.error == 1
    assert span.get_tag("http.status_code") == "500"
    assert span.get_tag(http.URL) == "http://testserver/error-500/"
    if django.VERSION >= (2, 2, 0):
        assert span.resource == "GET ^error-500/$"
    else:
        assert span.resource == "GET tests.contrib.django.views.error_500"
    assert span.get_tag(ERROR_MSG) is None
    assert span.get_tag(ERROR_TYPE) is None
    assert span.get_tag(ERROR_STACK) is None

    # Test the view span (where the exception is generated)
    view_span = list(test_spans.filter_spans(name="django.view"))
    assert len(view_span) == 1
    view_span = view_span[0]
    assert view_span.error == 1
    # Make sure the message is somewhere in the stack trace
    assert "Error 500" in view_span.get_tag(ERROR_STACK)

    # Test the catch exception middleware
    res = "tests.contrib.django.middleware.CatchExceptionMiddleware.process_exception"
    mw_span = list(test_spans.filter_spans(resource=res))[0]
    assert mw_span.error == 1
    # Make sure the message is somewhere in the stack trace
    assert "Error 500" in view_span.get_tag(ERROR_STACK)
    assert mw_span.get_tag(ERROR_MSG) is not None
    assert mw_span.get_tag(ERROR_TYPE) is not None
    assert mw_span.get_tag(ERROR_STACK) is not None


def test_middleware_handled_view_exception_success(client, test_spans):
    """
    When an exception is raised in a view and then handled
        Only the culprit span contains error properties
    """
    with modify_settings(
        **(
            dict(MIDDLEWARE={"append": "tests.contrib.django.middleware.HandleErrorMiddlewareSuccess"})
            if django.VERSION >= (2, 0, 0)
            else dict(MIDDLEWARE_CLASSES={"append": "tests.contrib.django.middleware.HandleErrorMiddlewareSuccess"})
        )
    ):
        assert client.get("/error-500/").status_code == 200

    error_spans = list(test_spans.filter_spans(error=1))
    # There should be 1 span flagged as erroneous:
    # - The view span which wraps the original error
    assert len(error_spans) == 1

    # Test the root span
    root_span = test_spans.get_root_span()
    assert root_span.error == 0
    assert root_span.get_tag(ERROR_STACK) is None
    assert root_span.get_tag(ERROR_MSG) is None
    assert root_span.get_tag(ERROR_TYPE) is None

    # Test the view span (where the exception is generated)
    view_span = list(test_spans.filter_spans(name="django.view"))
    assert len(view_span) == 1
    view_span = view_span[0]
    assert view_span.error == 1
    # Make sure the message is somewhere in the stack trace
    assert "Error 500" in view_span.get_tag("error.stack")


@pytest.mark.skipif(django.VERSION < (1, 10, 0), reason="Middleware functions only implemented since 1.10.0")
def test_empty_middleware_func_is_raised_in_django(client, test_spans):
    # Ensures potential empty middleware function (that returns None) is caught in django's end and not in our tracer
    with override_settings(MIDDLEWARE=["tests.contrib.django.middleware.empty_middleware"]):
        with pytest.raises(django.core.exceptions.ImproperlyConfigured):
            client.get("/")


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_multiple_fn_middleware_resource_names(client, test_spans):
    with modify_settings(MIDDLEWARE={"append": "tests.contrib.django.middleware.fn2_middleware"}):
        client.get("/")

    assert test_spans.find_span(name="django.middleware", resource="tests.contrib.django.middleware.fn_middleware")
    assert test_spans.find_span(name="django.middleware", resource="tests.contrib.django.middleware.fn2_middleware")


"""
View tests
"""


def test_request_view(client, test_spans):
    """
    When making a request to a Django app
        A `django.view` span is produced
    """
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    view_spans = list(test_spans.filter_spans(name="django.view"))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name="django.view",
        service="django",
        resource="tests.contrib.django.views.index",
        error=0,
    )


def test_class_views_resource_names(client, test_spans):
    """
    When making a request to a Django app and DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT=True
        The resource name of the django.request span should contain the name of the view
    """
    with override_config("django", dict(use_handler_resource_format=True)):
        # Send a request to a endpoint with a class based view
        resp = client.get("/simple/")
        assert resp.status_code == 200
        assert resp.content == b""

    view_spans = list(test_spans.filter_spans(name="django.view"))
    assert len(view_spans) == 1
    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name="django.view", service="django", resource="tests.contrib.django.views.BasicView", error=0
    )
    # Get request span
    request_span = list(test_spans.filter_spans(name="django.request"))[0]
    # Ensure resource name contains the view
    assert view_span.resource in request_span.resource


def test_lambda_based_view(client, test_spans):
    # ensures that the internals are properly traced when using a function view
    assert client.get("/lambda-view/").status_code == 200

    span = test_spans.get_root_span()
    assert span.get_tag("http.status_code") == "200"
    assert span.get_tag(http.URL) == "http://testserver/lambda-view/"
    if django.VERSION >= (2, 2, 0):
        assert span.resource == "GET ^lambda-view/$"
    else:
        assert span.resource == "GET tests.contrib.django.views.<lambda>"


def test_template_view(client, test_spans):
    resp = client.get("/template-view/")
    assert resp.status_code == 200
    assert resp.content == b"some content\n"

    view_spans = list(test_spans.filter_spans(name="django.view"))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name="django.view",
        service="django",
        resource="tests.contrib.django.views.template_view",
        error=0,
    )

    root_span = test_spans.get_root_span()
    assert root_span.get_tag("django.response.template") == "basic.html"


def test_template_simple_view(client, test_spans):
    resp = client.get("/template-simple-view/")
    assert resp.status_code == 200
    assert resp.content == b"some content\n"

    view_spans = list(test_spans.filter_spans(name="django.view"))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name="django.view",
        service="django",
        resource="tests.contrib.django.views.template_simple_view",
        error=0,
    )

    root_span = test_spans.get_root_span()
    assert root_span.get_tag("django.response.template") == "basic.html"


def test_template_list_view(client, test_spans):
    resp = client.get("/template-list-view/")
    assert resp.status_code == 200
    assert resp.content == b"some content\n"

    view_spans = list(test_spans.filter_spans(name="django.view"))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name="django.view",
        service="django",
        resource="tests.contrib.django.views.template_list_view",
        error=0,
    )

    root_span = test_spans.get_root_span()
    assert root_span.get_tag("django.response.template.0") == "doesntexist.html"
    assert root_span.get_tag("django.response.template.1") == "basic.html"


def test_middleware_trace_function_based_view(client, test_spans):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/fn-view/").status_code == 200

    span = test_spans.get_root_span()
    assert span.get_tag("http.status_code") == "200"
    assert span.get_tag(http.URL) == "http://testserver/fn-view/"
    if django.VERSION >= (2, 2, 0):
        assert span.resource == "GET ^fn-view/$"
    else:
        assert span.resource == "GET tests.contrib.django.views.function_view"


def test_middleware_trace_errors(client, test_spans):
    # ensures that the internals are properly traced
    assert client.get("/fail-view/").status_code == 403

    span = test_spans.get_root_span()
    assert span.get_tag("http.status_code") == "403"
    assert span.get_tag(http.URL) == "http://testserver/fail-view/"
    if django.VERSION >= (2, 2, 0):
        assert span.resource == "GET ^fail-view/$"
    else:
        assert span.resource == "GET tests.contrib.django.views.ForbiddenView"


def test_middleware_trace_staticmethod(client, test_spans):
    # ensures that the internals are properly traced
    assert client.get("/static-method-view/").status_code == 200

    span = test_spans.get_root_span()
    assert span.get_tag("http.status_code") == "200"
    assert span.get_tag(http.URL) == "http://testserver/static-method-view/"
    if django.VERSION >= (2, 2, 0):
        assert span.resource == "GET ^static-method-view/$"
    else:
        assert span.resource == "GET tests.contrib.django.views.StaticMethodView"


def test_simple_view_get(client, test_spans):
    # The `get` method of a view should be traced
    assert client.get("/simple/").status_code == 200
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.dispatch"))) == 1
    spans = list(test_spans.filter_spans(name="django.view.get"))
    assert len(spans) == 1
    span = spans[0]
    span.assert_matches(
        resource="tests.contrib.django.views.BasicView.get",
        error=0,
    )


def test_simple_view_post(client, test_spans):
    # The `post` method of a view should be traced
    assert client.post("/simple/").status_code == 200
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.dispatch"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.post"))) == 1
    spans = list(test_spans.filter_spans(name="django.view.post"))
    assert len(spans) == 1
    span = spans[0]
    span.assert_matches(
        resource="tests.contrib.django.views.BasicView.post",
        error=0,
    )


def test_simple_view_delete(client, test_spans):
    # The `delete` method of a view should be traced
    assert client.delete("/simple/").status_code == 200
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.dispatch"))) == 1
    spans = list(test_spans.filter_spans(name="django.view.delete"))
    assert len(spans) == 1
    span = spans[0]
    span.assert_matches(
        resource="tests.contrib.django.views.BasicView.delete",
        error=0,
    )


def test_simple_view_options(client, test_spans):
    # The `options` method of a view should be traced
    assert client.options("/simple/").status_code == 200
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.dispatch"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.options"))) == 1
    spans = list(test_spans.filter_spans(name="django.view.options"))
    assert len(spans) == 1
    span = spans[0]
    span.assert_matches(
        resource="django.views.generic.base.View.options",
        error=0,
    )


def test_simple_view_head(client, test_spans):
    # The `head` method of a view should be traced
    assert client.head("/simple/").status_code == 200
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1
    assert len(list(test_spans.filter_spans(name="django.view.dispatch"))) == 1
    spans = list(test_spans.filter_spans(name="django.view.head"))
    assert len(spans) == 1
    span = spans[0]
    span.assert_matches(
        resource="tests.contrib.django.views.BasicView.head",
        error=0,
    )


"""
Database tests
"""


@pytest.mark.django_db
def test_connection(client, test_spans):
    """
    When database queries are made from Django
        The queries are traced
    """
    from django.contrib.auth.models import User

    users = User.objects.count()
    assert users == 0

    test_spans.assert_span_count(1)
    spans = test_spans.get_spans()

    span = spans[0]
    assert span.name == "sqlite.query"
    assert span.service == "defaultdb"
    assert span.span_type == "sql"
    assert span.get_tag("django.db.vendor") == "sqlite"
    assert span.get_tag("django.db.alias") == "default"


"""
Caching tests
"""


def test_cache_get(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.get("missing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.get"
    assert span.name == "django.cache"
    assert span.span_type == "cache"
    assert span.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "missing_key",
    }

    assert_dict_issuperset(span.get_tags(), expected_meta)


def test_cache_service_schematization(test_spans):
    cache = django.core.cache.caches["default"]

    with override_config("django", dict(cache_service_name="test-cache-service")):
        cache.get("missing_key")
        spans = test_spans.get_spans()
        assert spans
        span = spans[0]
        expected_service_name = schematize_service_name(config.django.cache_service_name)
        assert span.service == expected_service_name


def test_cache_get_rowcount_existing_key(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.set("existing_key", 20)

    cache.get("existing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 2

    span = spans[1]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(span.get_metrics(), {"db.row_count": 1})


def test_cache_get_rowcount_missing_key(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.get("missing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(span.get_metrics(), {"db.row_count": 0})


class NoBool:
    def __bool__(self):
        raise NotImplementedError


def test_cache_get_rowcount_empty_key(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]
    cache.set(1, NoBool())

    result = cache.get(1)

    assert isinstance(result, NoBool) is True

    spans = test_spans.get_spans()
    assert len(spans) == 2

    get_span = spans[1]
    assert get_span.service == "django"
    assert get_span.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 1})


def test_cache_get_rowcount_missing_key_with_default(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    # This is the diff with `test_cache_get_rowcount_missing_key`,
    # we are setting a default value to be returned in case of a cache miss
    cache.get("missing_key", default="default_value")

    spans = test_spans.get_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(span.get_metrics(), {"db.row_count": 1})


class RaiseNotImplementedError:
    def __eq__(self, _):
        raise NotImplementedError


class RaiseValueError:
    def __eq__(self, _):
        raise ValueError


class RaiseAttributeError:
    def __eq__(self, _):
        raise AttributeError


def test_cache_get_rowcount_throws_attribute_and_value_error(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.set(1, RaiseNotImplementedError())
    cache.set(2, RaiseValueError())
    cache.set(3, RaiseAttributeError())

    # This is the diff with `test_cache_get_rowcount_missing_key`,
    # we are setting a default value to be returned in case of a cache miss
    result = cache.get(1)
    assert isinstance(result, RaiseNotImplementedError)

    result = cache.get(2)
    assert isinstance(result, RaiseValueError)

    result = cache.get(3)
    assert isinstance(result, RaiseAttributeError)

    spans = test_spans.get_spans()
    assert len(spans) == 6

    set_1 = spans[0]
    set_2 = spans[1]
    set_3 = spans[2]
    assert set_1.resource == "django.core.cache.backends.locmem.set"
    assert set_2.resource == "django.core.cache.backends.locmem.set"
    assert set_3.resource == "django.core.cache.backends.locmem.set"

    get_1 = spans[3]
    assert get_1.service == "django"
    assert get_1.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(get_1.get_metrics(), {"db.row_count": 0})

    get_2 = spans[4]
    assert get_2.service == "django"
    assert get_2.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(get_2.get_metrics(), {"db.row_count": 0})

    get_3 = spans[5]
    assert get_3.service == "django"
    assert get_3.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(get_3.get_metrics(), {"db.row_count": 0})


class MockDataFrame:
    def __init__(self, data):
        self.data = data

    def __eq__(self, other):
        if isinstance(other, str):
            return MockDataFrame([item == other for item in self.data])
        else:
            return MockDataFrame([row == other for row in self.data])

    def __bool__(self):
        raise ValueError("Cannot determine truthiness of comparison result for DataFrame.")

    def __iter__(self):
        return iter(self.data)


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_cache_get_rowcount_iterable_ambiguous_truthiness(test_spans):
    # get the default cache

    data = {"col1": 1, "col2": 2, "col3": 3}

    cache = django.core.cache.caches["default"]
    cache.set(1, MockDataFrame(data))
    cache.set(2, None)

    # throw error to verify that mock class has ambigiuous truthiness, django patch will do a similar
    # set of comparisons when trying to get rowcount
    with pytest.raises(ValueError):
        df = MockDataFrame(data)
        assert df is not None
        check_result = df == "some_result"
        if check_result:
            print("This should never print.")

    # Test to ensure that a DF does not throw a bool error when trying to
    # determine if the result was valid.
    result = cache.get(1)
    assert isinstance(result, MockDataFrame)

    result = cache.get(2)
    assert result is None

    spans = test_spans.get_spans()
    assert len(spans) == 4

    set_1 = spans[0]
    set_2 = spans[1]
    assert set_1.resource == "django.core.cache.backends.locmem.set"
    assert set_2.resource == "django.core.cache.backends.locmem.set"

    get_1 = spans[2]
    assert get_1.service == "django"
    assert get_1.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(get_1.get_metrics(), {"db.row_count": 1})

    get_2 = spans[3]
    assert get_2.service == "django"
    assert get_2.resource == "django.core.cache.backends.locmem.get"
    assert_dict_issuperset(get_2.get_metrics(), {"db.row_count": 0})


def test_cache_get_unicode(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.get("😐")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.get"
    assert span.name == "django.cache"
    assert span.span_type == "cache"
    assert span.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "😐",
    }

    assert_dict_issuperset(span.get_tags(), expected_meta)


def test_cache_set(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    # (trace) the cache miss
    cache.set("a_new_key", 50)

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.set"
    assert span.name == "django.cache"
    assert span.span_type == "cache"
    assert span.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "a_new_key",
    }

    assert_dict_issuperset(span.get_tags(), expected_meta)


def test_cache_delete(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.delete("an_existing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "django"
    assert span.resource == "django.core.cache.backends.locmem.delete"
    assert span.name == "django.cache"
    assert span.span_type == "cache"
    assert span.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "an_existing_key",
    }

    assert_dict_issuperset(span.get_tags(), expected_meta)


@pytest.mark.skipif(django.VERSION >= (2, 1, 0), reason="")
def test_cache_incr_1XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches["default"]
    cache.set("value", 0)
    test_spans.reset()

    cache.incr("value")

    spans = test_spans.get_spans()
    assert len(spans) == 2

    span_incr = spans[0]
    span_get = spans[1]

    # LocMemCache doesn't provide an atomic operation
    assert span_get.service == "django"
    assert span_get.resource == "django.core.cache.backends.locmem.get"
    assert span_get.name == "django.cache"
    assert span_get.span_type == "cache"
    assert span_get.error == 0
    assert span_incr.service == "django"
    assert span_incr.resource == "django.core.cache.backends.locmem.incr"
    assert span_incr.name == "django.cache"
    assert span_incr.span_type == "cache"
    assert span_incr.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "value",
    }

    assert_dict_issuperset(span_get.get_tags(), expected_meta)
    assert_dict_issuperset(span_incr.get_tags(), expected_meta)


@pytest.mark.skipif(django.VERSION < (2, 1, 0), reason="")
def test_cache_incr_2XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches["default"]
    cache.set("value", 0)
    test_spans.reset()

    cache.incr("value")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span_incr = spans[0]

    # LocMemCache doesn't provide an atomic operation
    assert span_incr.service == "django"
    assert span_incr.resource == "django.core.cache.backends.locmem.incr"
    assert span_incr.name == "django.cache"
    assert span_incr.span_type == "cache"
    assert span_incr.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "value",
    }

    assert_dict_issuperset(span_incr.get_tags(), expected_meta)


@pytest.mark.skipif(django.VERSION >= (2, 1, 0), reason="")
def test_cache_decr_1XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches["default"]
    cache.set("value", 0)
    test_spans.reset()

    cache.decr("value")

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_decr = spans[0]
    span_incr = spans[1]
    span_get = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_get.service == "django"
    assert span_get.resource == "django.core.cache.backends.locmem.get"
    assert span_get.name == "django.cache"
    assert span_get.span_type == "cache"
    assert span_get.error == 0
    assert span_incr.service == "django"
    assert span_incr.resource == "django.core.cache.backends.locmem.incr"
    assert span_incr.name == "django.cache"
    assert span_incr.span_type == "cache"
    assert span_incr.error == 0
    assert span_decr.service == "django"
    assert span_decr.resource == "django.core.cache.backends.base.decr"
    assert span_decr.name == "django.cache"
    assert span_decr.span_type == "cache"
    assert span_decr.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "value",
    }

    assert_dict_issuperset(span_get.get_tags(), expected_meta)
    assert_dict_issuperset(span_incr.get_tags(), expected_meta)
    assert_dict_issuperset(span_decr.get_tags(), expected_meta)


@pytest.mark.skipif(django.VERSION < (2, 1, 0), reason="")
def test_cache_decr_2XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches["default"]
    cache.set("value", 0)
    test_spans.reset()

    cache.decr("value")

    spans = test_spans.get_spans()
    assert len(spans) == 2

    span_decr = spans[0]
    span_incr = spans[1]

    # LocMemCache doesn't provide an atomic operation
    assert span_incr.service == "django"
    assert span_incr.resource == "django.core.cache.backends.locmem.incr"
    assert span_incr.name == "django.cache"
    assert span_incr.span_type == "cache"
    assert span_incr.error == 0
    assert span_decr.service == "django"
    assert span_decr.resource == "django.core.cache.backends.base.decr"
    assert span_decr.name == "django.cache"
    assert span_decr.span_type == "cache"
    assert span_decr.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "value",
    }

    assert_dict_issuperset(span_incr.get_tags(), expected_meta)
    assert_dict_issuperset(span_decr.get_tags(), expected_meta)


def test_cache_get_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.get_many(["missing_key", "another_key"])

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_get_many = spans[0]
    span_get_first = spans[1]
    span_get_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_get_first.service == "django"
    assert span_get_first.resource == "django.core.cache.backends.locmem.get"
    assert span_get_first.name == "django.cache"
    assert span_get_first.span_type == "cache"
    assert span_get_first.error == 0
    assert span_get_second.service == "django"
    assert span_get_second.resource == "django.core.cache.backends.locmem.get"
    assert span_get_second.name == "django.cache"
    assert span_get_second.span_type == "cache"
    assert span_get_second.error == 0
    assert span_get_many.service == "django"
    assert span_get_many.resource == "django.core.cache.backends.base.get_many"
    assert span_get_many.name == "django.cache"
    assert span_get_many.span_type == "cache"
    assert span_get_many.error == 0

    expected_meta = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "missing_key another_key",
    }

    assert_dict_issuperset(span_get_many.get_tags(), expected_meta)


def test_cache_get_many_rowcount_all_existing(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.set_many({"first_key": 1, "second_key": 2})

    cache.get_many(["first_key", "second_key"])

    spans = test_spans.get_spans()
    assert len(spans) == 6

    # spans in order: set_many, set1, set2, get_many, get1, get2
    span_get_many = spans[3]
    span_get_first = spans[4]
    span_get_second = spans[5]

    assert span_get_many.service == "django"
    assert span_get_many.resource == "django.core.cache.backends.base.get_many"
    assert span_get_first.service == "django"
    assert span_get_first.resource == "django.core.cache.backends.locmem.get"
    assert span_get_second.service == "django"
    assert span_get_second.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(span_get_many.get_metrics(), {"db.row_count": 2})
    assert_dict_issuperset(span_get_first.get_metrics(), {"db.row_count": 1})
    assert_dict_issuperset(span_get_second.get_metrics(), {"db.row_count": 1})


def test_cache_get_many_rowcount_none_existing(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    result = cache.get_many(["non-existent-key", "non-existent-key-2"])
    assert result == {}

    spans = test_spans.get_spans()
    assert len(spans) == 3

    # spans in order: set_many, set1, set2, get_many, get1, get2
    span_get_many = spans[0]
    span_get_first = spans[1]
    span_get_second = spans[2]

    assert span_get_many.service == "django"
    assert span_get_many.resource == "django.core.cache.backends.base.get_many"
    assert span_get_first.service == "django"
    assert span_get_first.resource == "django.core.cache.backends.locmem.get"
    assert span_get_second.service == "django"
    assert span_get_second.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(span_get_many.get_metrics(), {"db.row_count": 0})
    assert_dict_issuperset(span_get_first.get_metrics(), {"db.row_count": 0})
    assert_dict_issuperset(span_get_second.get_metrics(), {"db.row_count": 0})


def test_cache_get_many_rowcount_some_existing(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.set_many({"first_key": 1, "second_key": 2})

    result = cache.get_many(["first_key", "missing_key"])

    print(result)
    assert result == {"first_key": 1}

    spans = test_spans.get_spans()
    assert len(spans) == 6

    # spans in order: set_many, set1, set2, get_many, get1, get2
    span_get_many = spans[3]
    span_get_first = spans[4]
    span_get_second = spans[5]

    assert span_get_many.service == "django"
    assert span_get_many.resource == "django.core.cache.backends.base.get_many"
    assert span_get_first.resource == "django.core.cache.backends.locmem.get"
    assert span_get_second.service == "django"
    assert span_get_second.resource == "django.core.cache.backends.locmem.get"

    assert_dict_issuperset(span_get_many.get_metrics(), {"db.row_count": 1})
    assert_dict_issuperset(span_get_first.get_metrics(), {"db.row_count": 1})
    assert_dict_issuperset(span_get_second.get_metrics(), {"db.row_count": 0})


def test_cache_set_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.set_many({"first_key": 1, "second_key": 2})

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_set_many = spans[0]
    span_set_first = spans[1]
    span_set_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_set_first.service == "django"
    assert span_set_first.resource == "django.core.cache.backends.locmem.set"
    assert span_set_first.name == "django.cache"
    assert span_set_first.span_type == "cache"
    assert span_set_first.error == 0
    assert span_set_second.service == "django"
    assert span_set_second.resource == "django.core.cache.backends.locmem.set"
    assert span_set_second.name == "django.cache"
    assert span_set_second.span_type == "cache"
    assert span_set_second.error == 0
    assert span_set_many.service == "django"
    assert span_set_many.resource == "django.core.cache.backends.base.set_many"
    assert span_set_many.name == "django.cache"
    assert span_set_many.span_type == "cache"
    assert span_set_many.error == 0

    assert span_set_many.get_tag("django.cache.backend") == "django.core.cache.backends.locmem.LocMemCache"
    assert "first_key" in span_set_many.get_tag("django.cache.key")
    assert "second_key" in span_set_many.get_tag("django.cache.key")


def test_cache_delete_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches["default"]

    cache.delete_many(["missing_key", "another_key"])

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_delete_many = spans[0]
    span_delete_first = spans[1]
    span_delete_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_delete_first.service == "django"
    assert span_delete_first.resource == "django.core.cache.backends.locmem.delete"
    assert span_delete_first.name == "django.cache"
    assert span_delete_first.span_type == "cache"
    assert span_delete_first.error == 0
    assert span_delete_second.service == "django"
    assert span_delete_second.resource == "django.core.cache.backends.locmem.delete"
    assert span_delete_second.name == "django.cache"
    assert span_delete_second.span_type == "cache"
    assert span_delete_second.error == 0
    assert span_delete_many.service == "django"
    assert span_delete_many.resource == "django.core.cache.backends.base.delete_many"
    assert span_delete_many.name == "django.cache"
    assert span_delete_many.span_type == "cache"
    assert span_delete_many.error == 0

    assert span_delete_many.get_tag("django.cache.backend") == "django.core.cache.backends.locmem.LocMemCache"
    assert "missing_key" in span_delete_many.get_tag("django.cache.key")
    assert "another_key" in span_delete_many.get_tag("django.cache.key")


@pytest.mark.django_db
def test_cached_view(client, test_spans):
    # make the first request so that the view is cached
    response = client.get("/cached-users/")
    assert response.status_code == 200

    # check the first call for a non-cached view
    spans = list(test_spans.filter_spans(name="django.cache"))
    assert len(spans) == 3
    # the cache miss
    assert spans[0].resource == "django.core.cache.backends.locmem.get"
    # store the result in the cache
    assert spans[1].resource == "django.core.cache.backends.locmem.set"
    assert spans[2].resource == "django.core.cache.backends.locmem.set"

    # check if the cache hit is traced
    response = client.get("/cached-users/")
    assert response.status_code == 200
    spans = list(test_spans.filter_spans(name="django.cache"))
    # There should be two more spans now
    assert len(spans) == 5

    span_header = spans[3]
    span_view = spans[4]
    assert span_view.service == "django"
    assert span_view.resource == "django.core.cache.backends.locmem.get"
    assert span_view.name == "django.cache"
    assert span_view.span_type == "cache"
    assert span_view.error == 0
    assert span_header.service == "django"
    assert span_header.resource == "django.core.cache.backends.locmem.get"
    assert span_header.name == "django.cache"
    assert span_header.span_type == "cache"
    assert span_header.error == 0

    expected_meta_view = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": (
            "views.decorators.cache.cache_page..GET.03cdc1cc4aab71b038a6764e5fcabb82.d41d8cd98f00b204e9800998ecf8..."
        ),
        "_dd.base_service": "tests.contrib.django",
    }

    expected_meta_header = {
        "component": "django",
        "django.cache.backend": "django.core.cache.backends.locmem.LocMemCache",
        "django.cache.key": "views.decorators.cache.cache_header..03cdc1cc4aab71b038a6764e5fcabb82.en-us",
        "_dd.base_service": "tests.contrib.django",
    }

    assert span_view.get_tags() == expected_meta_view
    assert span_header.get_tags() == expected_meta_header


"""
Configuration tests
"""


def test_service_can_be_overridden(client, test_spans):
    with override_config("django", dict(service_name="test-service")):
        response = client.get("/")
        assert response.status_code == 200

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == "test-service"


@pytest.mark.parametrize("global_service_name", [None, "mysvc"])
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_default_service_name(
    ddtrace_run_python_code_in_subprocess, schema_version, global_service_name, request
):
    expected_service_name = {
        None: global_service_name or "django",
        "v0": global_service_name or "django",
        "v1": global_service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
    }[schema_version]
    code = """
import pytest
import sys

import django

from tests.contrib.django.conftest import *
from tests.utils import override_config

def test(client, test_spans):
    response = client.get("/")
    assert response.status_code == 200

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if global_service_name is not None:
        env["DD_SERVICE"] = global_service_name
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.parametrize(
    "schema_version, global_service_name",
    [(None, None), (None, "mysvc"), ("v0", None), ("v0", "mysvc"), ("v1", None), ("v1", "mysvc")],
)
def test_schematized_default_db_service_name(
    ddtrace_run_python_code_in_subprocess, schema_version, global_service_name, request
):
    expected_service_name = {
        None: "defaultdb",
        "v0": "defaultdb",
        "v1": global_service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
    }[schema_version]
    code = """
import pytest
import sys

import django

from tests.contrib.django.conftest import *
from tests.utils import override_config

@pytest.mark.django_db
def test_connection(client, test_spans):
    from django.contrib.auth.models import User

    users = User.objects.count()
    assert users == 0

    test_spans.assert_span_count(1)
    spans = test_spans.get_spans()

    span = spans[0]
    assert span.name == "sqlite.query"
    assert span.service == "{}", span.service
    assert span.span_type == "sql"
    assert span.get_tag("django.db.vendor") == "sqlite"
    assert span.get_tag("django.db.alias") == "default"

if __name__ == "__main__":
    # --reuse-db needed so the subprocess will not delete the main process database.
    sys.exit(pytest.main(["-x", "--reuse-db", __file__]))
    """.format(
        expected_service_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if global_service_name is not None:
        env["DD_SERVICE"] = global_service_name
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_operation_name(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_operation_name = {None: "django.request", "v0": "django.request", "v1": "http.server.request"}[
        schema_version
    ]
    code = """
import pytest
import sys

import django

from tests.contrib.django.conftest import *
from tests.utils import override_config

def test(client, test_spans):
    response = client.get("/")
    assert response.status_code == 200

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.name == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.django_db
def test_database_service_prefix_can_be_overridden(test_spans):
    with override_config("django", dict(database_service_name_prefix="my-")):
        from django.contrib.auth.models import User

        User.objects.count()

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == "my-defaultdb"


@pytest.mark.django_db
def test_database_service_can_be_overridden(test_spans):
    with override_config("django", dict(database_service_name="django-db")):
        from django.contrib.auth.models import User

        User.objects.count()

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == "django-db"


@pytest.mark.django_db
def test_database_service_prefix_precedence(test_spans):
    with override_config("django", dict(database_service_name="django-db", database_service_name_prefix="my-")):
        from django.contrib.auth.models import User

        User.objects.count()

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == "django-db"


def test_cache_service_can_be_overridden(test_spans):
    cache = django.core.cache.caches["default"]

    with override_config("django", dict(cache_service_name="test-cache-service")):
        cache.get("missing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == "test-cache-service"


def test_django_request_distributed(client, test_spans):
    """
    When making a request to a Django app
        With distributed tracing headers
            The django.request span properly inherits from the distributed trace
    """
    headers = {
        get_wsgi_header(HTTP_HEADER_TRACE_ID): "12345",
        get_wsgi_header(HTTP_HEADER_PARENT_ID): "78910",
        get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY): str(USER_KEEP),
    }
    resp = client.get("/", **headers)
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    # Assert that the trace properly inherits from the distributed headers
    # DEV: Do not use `test_spans.get_root_span()` since that expects `parent_id is None`
    root = test_spans.find_span(name="django.request")
    root.assert_matches(
        name="django.request",
        trace_id=12345,
        parent_id=78910,
        metrics={
            _SAMPLING_PRIORITY_KEY: USER_KEEP,
        },
    )
    assert root.get_tag("span.kind") == "server"
    first_child_span = test_spans.find_span(parent_id=root.span_id)
    assert first_child_span


def test_django_request_distributed_disabled(client, test_spans):
    """
    When making a request to a Django app
        With distributed tracing headers
            When distributed tracing is disabled
                The django.request span doesn't inherit from the distributed trace
    """
    headers = {
        get_wsgi_header(HTTP_HEADER_TRACE_ID): "12345",
        get_wsgi_header(HTTP_HEADER_PARENT_ID): "78910",
        get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY): str(USER_KEEP),
    }
    with override_config("django", dict(distributed_tracing_enabled=False)):
        resp = client.get("/", **headers)
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    # Assert the trace doesn't inherit from the distributed trace
    root = test_spans.find_span(name="django.request")
    assert root.get_tag("span.kind") == "server"
    assert root.trace_id != 12345
    assert root.parent_id is None


def test_inferred_spans_api_gateway_default(client, test_spans):
    """
    When making a request to a Django app,
        the django.request span properly inherits from the inferred spans
    """
    # must be in this form to override headers (workaround for python 3.7 django tests)
    # which doesn't have support to use headers kwarg in client.get()
    headers = {
        "HTTP_X_DD_PROXY": "aws-apigateway",
        "HTTP_X_DD_PROXY_REQUEST_TIME_MS": "1736973768000",
        "HTTP_X_DD_PROXY_PATH": "/",
        "HTTP_X_DD_PROXY_HTTPMETHOD": "GET",
        "HTTP_X_DD_PROXY_DOMAIN_NAME": "local",
        "HTTP_X_DD_PROXY_STAGE": "stage",
    }

    # When the inferred proxy feature is not enabled, there should be no inferred span
    resp = client.get("/", **headers)
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."
    web_span = test_spans.find_span(name="django.request")
    assert web_span._parent is None

    with override_global_config(dict(_inferred_proxy_services_enabled="true")):
        test_spans.reset()
        resp = client.get("/", **headers)
        web_span = test_spans.find_span(name="django.request")
        aws_gateway_span = test_spans.find_span(name="aws.apigateway")

        # Assert common behavior including aws gateway metadata and web span metadata
        assert_web_and_inferred_aws_api_gateway_span_data(
            aws_gateway_span,
            web_span,
            web_span_name="django.request",
            web_span_component="django",
            web_span_service_name="django",
            web_span_resource="GET ^$",
            api_gateway_service_name="local",
            api_gateway_resource="GET /",
            method="GET",
            status_code="200",
            url="local/",
            start=1736973768.0,
        )

        test_spans.reset()
        try:
            client.get("/error-500/", **headers)
        except Exception:
            web_span = test_spans.find_span(name="django.request")
            aws_gateway_span = test_spans.find_span(name="aws.apigateway")

            # Assert common behavior including aws gateway metadata and web span metadata
            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="django.request",
                web_span_component="django",
                web_span_service_name="django",
                web_span_resource="GET ^error-500/$",
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code="500",
                url="local/",
                start=1736973768.0,
            )


def test_inferred_spans_api_gateway_distributed_tracing(client, test_spans):
    """
    When making a request to a Django app with distributed tracing headers,
        the django.request span properly inherits from the inferred spans
        and the inferred span inherits from the trace
    """
    # must be in this form to override headers (workaround for python 3.7 django tests)
    # which doesn't have support to use headers kwarg in client.get()
    test_headers = {
        "HTTP_X_DD_PROXY": "aws-apigateway",
        "HTTP_X_DD_PROXY_REQUEST_TIME_MS": "1736973768000",
        "HTTP_X_DD_PROXY_PATH": "/",
        "HTTP_X_DD_PROXY_HTTPMETHOD": "GET",
        "HTTP_X_DD_PROXY_DOMAIN_NAME": "local",
        "HTTP_X_DD_PROXY_STAGE": "stage",
        "HTTP_X_DATADOG_TRACE_ID": "1",
        "HTTP_X_DATADOG_PARENT_ID": "2",
        "HTTP_X_DATADOG_ORIGIN": "rum",
        "HTTP_X_DATADOG_SAMPLING_PRIORITY": "2",
    }
    # Without the feature enabled, we should not be creating the inferred span
    resp = client.get("/", **test_headers)
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    web_span = test_spans.find_span(name="django.request")
    aws_gateway_span = web_span._parent
    assert aws_gateway_span is None
    web_span.assert_matches(
        name="django.request",
        trace_id=1,
        parent_id=2,
        metrics={
            _SAMPLING_PRIORITY_KEY: USER_KEEP,
        },
    )

    with override_global_config(dict(_inferred_proxy_services_enabled="true")):
        test_spans.reset()
        client.get("/", **test_headers)
        web_span = test_spans.find_span(name="django.request")
        aws_gateway_span = web_span._parent

        # Assert common behavior including aws gateway metadata and web span metadata
        assert_web_and_inferred_aws_api_gateway_span_data(
            aws_gateway_span,
            web_span,
            web_span_name="django.request",
            web_span_component="django",
            web_span_service_name="django",
            web_span_resource="GET ^$",
            api_gateway_service_name="local",
            api_gateway_resource="GET /",
            method="GET",
            status_code="200",
            url="local/",
            start=1736973768.0,
            is_distributed=True,
            distributed_trace_id=1,
            distributed_parent_id=2,
            distributed_sampling_priority=USER_KEEP,
        )

        # No test for SAMPLING_PRIORITY_KEY because it doesn't appear when the web span is a child
        web_span.assert_matches(
            name="django.request",
            trace_id=1,
        )
        assert len(test_spans.spans) == 27


def test_trace_query_string_integration_enabled(client, test_spans):
    with override_http_config("django", dict(trace_query_string=True)):
        assert client.get("/?key1=value1&key2=value2").status_code == 200

    sp_request = test_spans.get_root_span()
    assert sp_request.get_tag("span.kind") == "server"
    assert sp_request.name == "django.request"
    assert sp_request.get_tag(http.QUERY_STRING) == "key1=value1&key2=value2"


def test_disabled_caches(client, test_spans):
    with override_config("django", dict(instrument_caches=False)):
        cache = django.core.cache.caches["default"]
        cache.get("missing_key")

    spans = test_spans.get_spans()
    assert len(spans) == 0


"""
Template tests
"""


def test_template(test_spans):
    # prepare a base template using the default engine
    template = django.template.Template("Hello {{name}}!")
    ctx = django.template.Context({"name": "Django"})

    assert template.render(ctx) == "Hello Django!"

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.span_type == "template"
    assert span.name == "django.template.render"

    template.name = "my-template"
    assert template.render(ctx) == "Hello Django!"
    spans = test_spans.get_spans()
    assert len(spans) == 2

    span = spans[1]
    assert span.get_tag("django.template.name") == "my-template"


def test_template_no_instrumented(test_spans):
    """
    When rendering templates with instrument_templates option disabled

    This test assert that changing the value at runtime/after patching
    properly disables template spans.
    """
    # prepare a base template using the default engine
    with override_config("django", dict(instrument_templates=False)):
        template = django.template.Template("Hello {{name}}!")
        ctx = django.template.Context({"name": "Django"})

        assert template.render(ctx) == "Hello Django!"
        spans = test_spans.get_spans()
        assert len(spans) == 0

        template.name = "my-template"
        assert template.render(ctx) == "Hello Django!"
        spans = test_spans.get_spans()
        assert len(spans) == 0


def test_template_name(test_spans):
    from pathlib import PosixPath

    # prepare a base template using the default engine
    template = django.template.Template("Hello {{name}}!")

    # DEV: template.name can be an instance of PosixPath (see
    # https://github.com/DataDog/dd-trace-py/issues/2418)
    template.name = PosixPath("/my-template")
    template.render(django.template.Context({"name": "Django"}))

    spans = test_spans.get_spans()
    assert len(spans) == 1

    (span,) = spans
    assert span.get_tag("django.template.name") == "/my-template"
    assert span.resource == "/my-template"


"""
OpenTracing tests
"""


@pytest.mark.django_db
def test_middleware_trace_request_ot(client, test_spans, tracer):
    """OpenTracing version of test_middleware_trace_request."""
    ot_tracer = init_tracer("my_svc", tracer)

    # ensures that the internals are properly traced
    with ot_tracer.start_active_span("ot_span"):
        assert client.get("/users/").status_code == 200

    # check for spans
    spans = test_spans.get_spans()
    ot_span = spans[0]
    sp_request = spans[1]

    # confirm parenting
    assert ot_span.parent_id is None
    assert sp_request.parent_id == ot_span.span_id

    assert ot_span.resource == "ot_span"
    assert ot_span.service == "my_svc"

    assert sp_request.get_tag("http.status_code") == "200"
    assert sp_request.get_tag(http.URL) == "http://testserver/users/"
    assert sp_request.get_tag("django.user.is_authenticated") == "False"
    assert sp_request.get_tag("http.method") == "GET"


def test_collecting_requests_handles_improperly_configured_error(client, test_spans):
    """
    Since it's difficult to reproduce the ImproperlyConfigured error via django (server setup), will instead
    mimic the failure by mocking the user_is_authenticated to raise an error.
    """
    # patch django._patch - django.__init__.py imports patch.py module as _patch
    with mock.patch(
        "ddtrace.contrib.internal.django.utils.user_is_authenticated",
        side_effect=django.core.exceptions.ImproperlyConfigured,
    ):
        # If ImproperlyConfigured error bubbles up, should automatically fail the test.
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."

        # Assert the correct number of traces and spans
        if django.VERSION >= (2, 0, 0):
            test_spans.assert_span_count(26)
        elif django.VERSION < (1, 11, 0):
            test_spans.assert_span_count(15)
        else:
            test_spans.assert_span_count(16)

        root = test_spans.get_root_span()
        root.assert_matches(name="django.request")


"""
urlpatterns tests
There are a variety of ways a user can point to their views.
"""


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="path only exists in >=2.0.0")
def test_urlpatterns_path(client, test_spans):
    """
    When a view is specified using `django.urls.path`
        The view is traced
    """
    assert client.get("/path/").status_code == 200

    # Ensure the view was traced
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="repath only exists in >=2.0.0")
def test_urlpatterns_repath(client, test_spans):
    """
    When a view is specified using `django.urls.repath`
        The view is traced
    """
    assert client.get("/re-path123/").status_code == 200

    # Ensure the view was traced
    assert len(list(test_spans.filter_spans(name="django.view"))) == 1


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
@pytest.mark.django_db
def test_user_name_included(client, test_spans):
    """
    When making a request to a Django app with user name included
        We correctly add the `django.user.name` tag to the root span
    """
    resp = client.get("/authenticated/")
    assert resp.status_code == 200

    # user name should be present in root span tags
    root = test_spans.get_root_span()
    assert root.get_tag("django.user.name") == "Jane Doe"
    assert root.get_tag("django.user.is_authenticated") == "True"
    assert root.get_tag(user.ID) == "1"


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
@pytest.mark.django_db
def test_user_name_excluded(client, test_spans):
    """
    When making a request to a Django app with user name excluded
        We correctly omit the `django.user.name` tag to the root span
    """
    with override_config("django", dict(include_user_name=False)):
        resp = client.get("/authenticated/")
    assert resp.status_code == 200

    # user name should not be present in root span tags
    root = test_spans.get_root_span()
    assert "django.user.name" not in root.get_tags()
    assert root.get_tag("django.user.is_authenticated") == "True"
    assert root.get_tag(user.ID) == "1"


def test_django_use_handler_resource_format(client, test_spans):
    """
    Test that the specified format is used over the default.
    """
    with override_config("django", dict(use_handler_resource_format=True)):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."

        # Assert the structure of the root `django.request` span
        root = test_spans.get_root_span()
        resource = "GET tests.contrib.django.views.index"

        root.assert_matches(resource=resource, parent_id=None, span_type="web")
        if django.VERSION >= (2, 2, 0):
            root.assert_meta({"http.route": "^$"})


def test_django_use_handler_with_url_name_resource_format(client, test_spans):
    """
    Test that the specified format is used over the default.
    """
    with override_config("django", dict(use_handler_with_url_name_resource_format=True)):
        resp = client.get("/fail-view/")
        assert resp.status_code == 403

        # Assert the structure of the root `django.request` span
        root = test_spans.get_root_span()
        resource = "GET tests.contrib.django.views.ForbiddenView.forbidden-view"
        if django.VERSION >= (2, 2, 0):
            resource = "GET ^fail-view/$"

        root.assert_matches(resource=resource, parent_id=None, span_type="web")


def test_django_use_handler_resource_format_env(client, test_spans):
    """
    Test that the specified format is used over the default.
    """
    with override_env(dict(DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT="true")):
        out = subprocess.check_output(
            [
                "python",
                "-c",
                (
                    "from ddtrace import config; "
                    "from ddtrace._monkey import _patch_all; _patch_all(); "
                    "import django; "
                    "assert config.django.use_handler_resource_format; print('Test success')"
                ),
            ]
        )
        assert out.startswith(b"Test success")


def test_django_use_handler_with_url_name_resource_format_env(client, test_spans):
    """
    Test that the specified format is used over the default.
    """
    with override_env(dict(DD_DJANGO_USE_HANDLER_WITH_URL_NAME_RESOURCE_FORMAT="true")):
        out = subprocess.check_output(
            [
                "python",
                "-c",
                (
                    "from ddtrace import config; "
                    "from ddtrace._monkey import _patch_all; _patch_all(); "
                    "import django; "
                    "assert config.django.use_handler_with_url_name_resource_format; print('Test success')"
                ),
            ]
        )
        assert out.startswith(b"Test success")


@pytest.mark.parametrize(
    "env_var,instrument_x",
    [
        ("DD_DJANGO_INSTRUMENT_DATABASES", "instrument_databases"),
        ("DD_DJANGO_INSTRUMENT_CACHES", "instrument_caches"),
        ("DD_DJANGO_INSTRUMENT_MIDDLEWARE", "instrument_middleware"),
        ("DD_DJANGO_INSTRUMENT_TEMPLATES", "instrument_templates"),
    ],
)
def test_enable_django_instrument_env(env_var, instrument_x, ddtrace_run_python_code_in_subprocess):
    """
    Test that {env} enables instrumentation
    """

    env = os.environ.copy()
    env[env_var] = "true"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        "import ddtrace;import django;assert ddtrace.config.django.{}".format(instrument_x),
        env=env,
    )

    assert status == 0, (out, err)


@pytest.mark.parametrize(
    "env_var,instrument_x",
    [
        ("DD_DJANGO_INSTRUMENT_DATABASES", "instrument_databases"),
        ("DD_DJANGO_INSTRUMENT_CACHES", "instrument_caches"),
        ("DD_DJANGO_INSTRUMENT_MIDDLEWARE", "instrument_middleware"),
        ("DD_DJANGO_INSTRUMENT_TEMPLATES", "instrument_templates"),
    ],
)
def test_disable_django_instrument_env(env_var, instrument_x, ddtrace_run_python_code_in_subprocess):
    """
    Test that {env} disables instrumentation
    """

    env = os.environ.copy()
    env[env_var] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        "import ddtrace;import django;assert not ddtrace.config.django.{}".format(instrument_x),
        env=env,
    )

    assert status == 0, (out, err)


def test_django_use_legacy_resource_format(client, test_spans):
    """
    Test that the specified format is used over the default.
    """
    with override_config("django", dict(use_legacy_resource_format=True)):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content == b"Hello, test app."

        # Assert the structure of the root `django.request` span
        root = test_spans.get_root_span()
        resource = "tests.contrib.django.views.index"

        root.assert_matches(resource=resource, parent_id=None, span_type="web")


def test_django_use_legacy_resource_format_env(client, test_spans):
    with override_env(dict(DD_DJANGO_USE_LEGACY_RESOURCE_FORMAT="true")):
        out = subprocess.check_output(
            [
                "python",
                "-c",
                (
                    "from ddtrace import config; "
                    "from ddtrace._monkey import _patch_all; _patch_all(); "
                    "import django; "
                    "assert config.django.use_legacy_resource_format; print('Test success')"
                ),
            ],
        )
        assert out.startswith(b"Test success")


def test_custom_dispatch_template_view(client, test_spans):
    """
    Test that a template view with a custom dispatch method inherited from a
    mixin is called.
    """
    resp = client.get("/composed-template-view/")
    assert resp.status_code == 200
    assert resp.content.strip() == b"custom dispatch 2"

    spans = test_spans.get_spans()
    assert [s.resource for s in spans if s.resource.endswith("dispatch")] == [
        "tests.contrib.django.views.CustomDispatchMixin.dispatch",
        "tests.contrib.django.views.AnotherCustomDispatchMixin.dispatch",
        "django.views.generic.base.View.dispatch",
    ]


def test_custom_dispatch_get_view(client, test_spans):
    """
    Test that a get method on a view with a custom dispatch method inherited
    from a mixin is called.
    """
    resp = client.get("/composed-get-view/")
    assert resp.status_code == 200
    assert resp.content.strip() == b"custom get"

    spans = test_spans.get_spans()
    assert [s.resource for s in spans if s.resource.endswith("dispatch")] == [
        "tests.contrib.django.views.CustomDispatchMixin.dispatch",
        "django.views.generic.base.View.dispatch",
    ]
    assert [s.resource for s in spans if s.resource.endswith("get")] == [
        "tests.contrib.django.views.ComposedGetView.get",
        "tests.contrib.django.views.CustomGetView.get",
    ]


def test_view_mixin(client, test_spans):
    from tests.contrib.django import views

    assert views.DISPATCH_CALLED is False
    resp = client.get("/composed-view/")
    assert views.DISPATCH_CALLED is True

    assert resp.status_code == 200
    assert resp.content.strip() == b"custom dispatch"


def test_template_view_patching():
    """
    Test to ensure that patching a view does not give it properties it did not have before
    """
    # DEV: `vars(cls)` will give you only the properties defined on that class,
    #      it will not traverse through __mro__ to find the property from a parent class

    # We are not starting with a "dispatch" property
    assert "dispatch" not in vars(TemplateView)

    # Manually call internal method for patching
    instrument_view(django, TemplateView)
    assert "dispatch" not in vars(TemplateView)

    # Patch via `as_view()`
    TemplateView.as_view()
    assert "dispatch" not in vars(TemplateView)


class _MissingSchemeRequest(django.http.HttpRequest):
    @property
    def scheme(self):
        pass


class _HttpRequest(django.http.HttpRequest):
    @property
    def scheme(self):
        return b"http"


@pytest.mark.parametrize(
    "request_cls,request_path,http_host",
    itertools.product(
        [django.http.HttpRequest, _HttpRequest, _MissingSchemeRequest],
        ["/;some/?awful/=path/foo:bar/", b"/;some/?awful/=path/foo:bar/"],
        ["testserver", b"testserver", SimpleLazyObject(lambda: "testserver"), SimpleLazyObject(lambda: object())],
    ),
)
def test_helper_get_request_uri(request_cls, request_path, http_host):
    def eval_lazy(lo):
        return str(lo) if issubclass(lo.__class__, str) else bytes(lo)

    request = request_cls()
    request.path = request_path
    request.META = {"HTTP_HOST": http_host}
    request_uri = get_request_uri(request)
    if (
        isinstance(http_host, SimpleLazyObject) and not (issubclass(http_host.__class__, str))
    ) or request_cls is _MissingSchemeRequest:
        assert request_uri is None
    else:
        assert (
            request_cls == _HttpRequest
            and isinstance(request_path, bytes)
            and isinstance(http_host, bytes)
            and isinstance(request_uri, bytes)
        ) or isinstance(request_uri, str)

        host = ensure_text(eval_lazy(http_host)) if isinstance(http_host, SimpleLazyObject) else http_host
        assert request_uri == "".join(map(ensure_text, (request.scheme, "://", host, request_path)))


@pytest.fixture()
def resource():
    # setUp and tearDown for TestWSGI test cases.
    request_started.disconnect(close_old_connections)
    yield
    request_started.connect(close_old_connections)


class TestWSGI:
    request_factory = RequestFactory()

    def test_get_wsgi_application_200_request(self, test_spans, resource):
        application = get_wsgi_application()
        test_response = {}
        environ = self.request_factory._base_environ(
            PATH_INFO="/", CONTENT_TYPE="text/html; charset=utf-8", REQUEST_METHOD="GET"
        )

        def start_response(status, headers):
            test_response["status"] = status
            test_response["headers"] = headers

        response = application(environ, start_response)

        expected_headers = [
            ("my-response-header", "my_response_value"),
            ("Content-Type", "text/html; charset=utf-8"),
        ]
        assert test_response["status"] == "200 OK"
        assert all([header in test_response["headers"] for header in expected_headers])
        assert response.content == b"Hello, test app."

        # Assert the structure of the root `django.request` span
        root = test_spans.get_root_span()

        if django.VERSION >= (2, 2, 0):
            resource = "GET ^$"
        else:
            resource = "GET tests.contrib.django.views.index"

        meta = {
            "component": "django",
            "django.request.class": "django.core.handlers.wsgi.WSGIRequest",
            "django.response.class": "django.http.response.HttpResponse",
            "django.user.is_authenticated": "False",
            "django.view": "tests.contrib.django.views.index",
            "http.method": "GET",
            "http.status_code": "200",
            "http.url": "http://testserver/",
            "span.kind": "server",
        }
        if django.VERSION >= (2, 2, 0):
            meta["http.route"] = "^$"

        assert http.QUERY_STRING not in root.get_tags()
        root.assert_matches(
            name="django.request",
            service="django",
            resource=resource,
            parent_id=None,
            span_type="web",
            error=0,
            meta=meta,
        )

    def test_get_wsgi_application_500_request(self, test_spans, resource):
        application = get_wsgi_application()
        test_response = {}
        environ = self.request_factory._base_environ(
            PATH_INFO="/error-500/", CONTENT_TYPE="text/html; charset=utf-8", REQUEST_METHOD="GET"
        )

        def start_response(status, headers):
            test_response["status"] = status
            test_response["headers"] = headers

        response = application(environ, start_response)
        assert test_response["status"].upper() == "500 INTERNAL SERVER ERROR"
        assert "Server Error" in str(response.content)

        # Assert the structure of the root `django.request` span
        root = test_spans.get_root_span()

        assert root.error == 1
        assert root.get_tag("http.status_code") == "500"
        assert root.get_tag(http.URL) == "http://testserver/error-500/"
        assert root.get_tag("django.response.class") == "django.http.response.HttpResponseServerError"
        if django.VERSION >= (2, 2, 0):
            assert root.resource == "GET ^error-500/$"
        else:
            assert root.resource == "GET tests.contrib.django.views.error_500"


@pytest.mark.django_db
def test_connections_patched():
    from django.db import connection
    from django.db import connections

    assert len(connections.all())
    for conn in connections.all():
        assert isinstance(conn.cursor, wrapt.ObjectProxy)

    assert isinstance(connection.cursor, wrapt.ObjectProxy)


def test_django_get_user(client, test_spans):
    assert client.get("/identify/").status_code == 200

    root = test_spans.get_root_span()

    # Values defined in tests/contrib/django/views.py::identify
    assert root.get_tag(user.ID) == "usr.id"
    assert root.get_tag(user.EMAIL) == "usr.email"
    assert root.get_tag(user.SESSION_ID) == "usr.session_id"
    assert root.get_tag(user.NAME) == "usr.name"
    assert root.get_tag(user.ROLE) == "usr.role"
    assert root.get_tag(user.SCOPE) == "usr.scope"


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason="")
def test_django_base_handler_failure(client, test_spans):
    """
    This tests the failure mode seen with Gunicorn during timeouts, where the Django
    Handler is terminated after <x> seconds and no response is returned by
    django.core.handler.base.BaseHandler.get_response.  We expect to populate the resource
    with "GET <resource>" instead of what we did before this test "GET"
    """
    trace_utils.unwrap(client.handler, "get_response")
    with mock.patch.object(client.handler, "get_response", side_effect=Exception("test")):
        trace_utils.wrap(client.handler, "get_response", traced_get_response(django))
        try:
            client.get("/")
        except Exception:
            pass  # We expect an error
        root = test_spans.get_root_span()
        assert root.resource == "GET ^$"
