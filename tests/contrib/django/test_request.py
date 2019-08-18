from contextlib import contextmanager

import django
import pytest

from ddtrace import Pin
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.ext import http
from ddtrace.ext.priority import USER_KEEP
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID, HTTP_HEADER_PARENT_ID, HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.utils import get_wsgi_header


def assert_trace_count(test_spans):
    test_spans.assert_trace_count(1)

    span_count = -1
    if django.VERSION >= (2, 0, 0):
        span_count = 10
    elif django.VERSION < (1, 10, 0):
        span_count = 15
    else:
        span_count = 16
    test_spans.assert_span_count(span_count)


@contextmanager
def pin_disabled():
    pin = Pin.get_from(django)
    pin.tracer.enabled = False
    try:
        yield
    finally:
        pin.tracer.enabled = True


def test_django_request_root_span(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.request` root span
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()

    if django.VERSION >= (2, 2, 0):
        resource = 'GET ^/'
    else:
        resource = 'GET tests.contrib.django.views.index'

    meta = {
        'django.request.class': 'django.core.handlers.wsgi.WSGIRequest',
        'django.response.class': 'django.http.response.HttpResponse',
        'django.view': 'tests.contrib.django.views.index',
        'http.method': 'GET',
        'http.status_code': '200',
        'http.url': 'http://testserver/',
    }
    if django.VERSION >= (2, 2, 0):
        meta['http.route'] = '^/'

    root.assert_matches(
        name='django.request',
        service='django',
        resource=resource,
        parent_id=None,
        span_type=http.TYPE,
        error=0,
        meta=meta,
    )


def test_django_request_root_span_pin_disabled(client, test_spans):
    """
    When making a request to a Django app
        When we disable the tracer via the Pin
            We do not create any spans
    """
    with pin_disabled():
        resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    test_spans.assert_trace_count(0)
    test_spans.assert_span_count(0)


@pytest.mark.skipif(
    django.VERSION < (2, 0, 0),
    reason='Skipping Django >= 2.0.0 test with Django {}'.format(django.VERSION),
)
def test_django_request_middleware_2_0_0(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name='django.middleware'))
    assert len(middleware_spans) == 8

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name='django.middleware',
            service='django',
            error=0,
            span_type=None,
        )

    span_resources = set([
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.core.handlers.base._get_response',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.middleware.security.SecurityMiddleware',
    ])
    assert set([s.resource for s in middleware_spans]) == span_resources

    # Get middleware spans in reverse order of start time
    middleware_spans = sorted(middleware_spans, key=lambda s: s.start, reverse=True)

    # Assert each middleware span starts after it's parent and stops before it's parent
    for i in range(0, len(middleware_spans) - 1):
        span = middleware_spans[i]
        previous_span = middleware_spans[i + 1]
        assert span.start > previous_span.start
        assert span.duration < previous_span.duration

    # Assert the first middleware span's parent is the root span (django.request)
    root_span = test_spans.get_root_span()
    assert root_span.name == 'django.request'
    first_middleware = middleware_spans[-1]
    assert first_middleware.parent_id == root_span.span_id


@pytest.mark.skipif(
    django.VERSION < (1, 10, 0) or django.VERSION > (2, 0, 0),
    reason='Skipping Django >=1.10.0,<2.0.0 test with Django {}'.format(django.VERSION),
)
def test_django_request_middleware_1_10_0(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name='django.middleware'))
    assert len(middleware_spans) == 14

    root = test_spans.get_root_span()
    root.assert_matches(name='django.request')

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name='django.middleware',
            service='django',
            error=0,
            span_type=None,
            parent_id=root.span_id,  # They are all children of the root django.request
        )

    # DEV: Order matters here, we want all `process_request` before `process_view`, before `process_response`
    expected_resources = [
        'django.contrib.sessions.middleware.process_request',
        'django.middleware.common.process_request',
        'django.middleware.csrf.process_request',
        'django.contrib.auth.middleware.process_request',
        'django.contrib.auth.middleware.process_request',
        'django.contrib.messages.middleware.process_request',
        'django.middleware.security.process_request',
        'django.middleware.csrf.process_view',
        'django.middleware.security.process_response',
        'django.middleware.clickjacking.process_response',
        'django.contrib.messages.middleware.process_response',
        'django.middleware.csrf.process_response',
        'django.middleware.common.process_response',
        'django.contrib.sessions.middleware.process_response',
    ]

    middleware_spans = sorted(middleware_spans, key=lambda s: s.start)
    span_resources = [s.resource for s in middleware_spans]
    assert span_resources == expected_resources


@pytest.mark.skipif(
    django.VERSION >= (1, 10, 0),
    reason='Skipping Django >= 1.10.0 test with Django {}'.format(django.VERSION),
)
def test_django_request_middleware_1_0_0(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name='django.middleware'))
    assert len(middleware_spans) == 13

    root = test_spans.get_root_span()
    root.assert_matches(name='django.request')

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name='django.middleware',
            service='django',
            error=0,
            span_type=None,
            parent_id=root.span_id,  # They are all children of the root django.request
        )

    # DEV: Order matters here, we want all `process_request` before `process_view`, before `process_response`
    expected_resources = [
        'django.contrib.sessions.middleware.process_request',
        'django.middleware.common.process_request',
        # 'django.middleware.csrf.process_request',  # Not in <1.10.0
        'django.contrib.auth.middleware.process_request',
        'django.contrib.auth.middleware.process_request',
        'django.contrib.messages.middleware.process_request',
        'django.middleware.security.process_request',
        'django.middleware.csrf.process_view',
        'django.middleware.security.process_response',
        'django.middleware.clickjacking.process_response',
        'django.contrib.messages.middleware.process_response',
        'django.middleware.csrf.process_response',
        'django.middleware.common.process_response',
        'django.contrib.sessions.middleware.process_response',
    ]

    middleware_spans = sorted(middleware_spans, key=lambda s: s.start)
    span_resources = [s.resource for s in middleware_spans]
    assert span_resources == expected_resources


@pytest.mark.skipif(
    django.VERSION < (2, 0, 0),
    reason='Skipping Django < 2.0.0 test with Django {}'.format(django.VERSION),
)
def test_django_request_view_2_0_0(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.view` span
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    view_spans = list(test_spans.filter_spans(name='django.view'))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name='django.view',
        service='django',
        resource='tests.contrib.django.views.index',
        error=0,
    )

    # Assert we are a child of the last middleware span
    last_middleware_span = None
    for span in test_spans.filter_spans(name='django.middleware'):
        if not last_middleware_span:
            last_middleware_span = span
        elif span.start > last_middleware_span.start:
            last_middleware_span = span

    assert view_span.parent_id == last_middleware_span.span_id


@pytest.mark.skipif(
    django.VERSION >= (2, 0, 0),
    reason='Skipping Django < 2.0.0 test with Django {}'.format(django.VERSION),
)
def test_django_request_view_1_0_0(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.view` span
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    view_spans = list(test_spans.filter_spans(name='django.view'))
    assert len(view_spans) == 1

    # Assert span properties
    view_span = view_spans[0]
    view_span.assert_matches(
        name='django.view',
        service='django',
        resource='tests.contrib.django.views.index',
        error=0,
    )

    # Assert we are a child of the root django.request span
    root = test_spans.get_root_span()
    root.assert_matches(name='django.request')
    assert view_span.parent_id == root.span_id


def test_django_request_distributed(client, test_spans):
    """
    When making a request to a Django app
        With distributed tracing headers
            The django.request span properly inherits from the distributed trace
    """
    headers = {
        get_wsgi_header(HTTP_HEADER_TRACE_ID): '12345',
        get_wsgi_header(HTTP_HEADER_PARENT_ID): '78910',
        get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY): USER_KEEP,
    }
    resp = client.get('/', **headers)
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Assert we properly inherit
    # DEV: Do not use `test_spans.get_root_span()` since that expects `parent_id is None`
    root = test_spans.find_span(name='django.request')
    root.assert_matches(
        name='django.request',
        trace_id=12345,
        parent_id=78910,
        metrics={
            SAMPLING_PRIORITY_KEY: USER_KEEP,
        },
    )


def test_django_request_not_found(client, test_spans):
    """
    When making a request to a Django app
        When the endpoint doesn't exist
            We create a 404 span
    """
    resp = client.get('/unknown/endpoint')
    assert resp.status_code == 404

    content = b'<h1>Not Found</h1><p>The requested resource was not found on this server.</p>'
    if django.VERSION < (1, 10, 0):
        content = b'<h1>Not Found</h1><p>The requested URL /unknown/endpoint was not found on this server.</p>'
    elif django.VERSION >= (3, 0, 0):
        content = (
            b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Not Found</title>\n'
            b'</head>\n<body>\n  <h1>Not Found</h1><p>The requested resource was not found '
            b'on this server.</p>\n</body>\n</html>\n'
        )
    assert resp.content == content

    # Assert the correct number of traces and spans
    test_spans.assert_trace_count(1)

    span_count = -1
    if django.VERSION >= (2, 0, 0):
        span_count = 10
    elif django.VERSION >= (1, 10, 0):
        span_count = 15
    else:
        span_count = 14
    test_spans.assert_span_count(span_count)

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()
    root.assert_matches(
        name='django.request',
        service='django',
        resource='GET 404',
        parent_id=None,
        span_type=http.TYPE,
        error=0,
        meta={
            'django.request.class': 'django.core.handlers.wsgi.WSGIRequest',
            'django.response.class': 'django.http.response.HttpResponseNotFound',
            'http.method': 'GET',
            'http.status_code': '404',
            'http.url': 'http://testserver/unknown/endpoint',
        },
    )

    # Assert 404 exception being raised
    if django.VERSION >= (2, 0, 0):
        error_spans = list(test_spans.filter_spans(error=1))
        assert len(error_spans) == 1

        error_span = error_spans[0]
        error_span.assert_matches(
            name='django.middleware',
            resource='django.core.handlers.base._get_response',
            error=1,
        )
        assert 'error.msg' in error_span.meta
        assert 'error.stack' in error_span.meta
        assert 'error.type' in error_span.meta

    # Assert template render
    render_spans = list(test_spans.filter_spans(name='django.template.render'))
    assert len(render_spans) == 1

    render_span = render_spans[0]
    render_span.assert_matches(
        name='django.template.render',
        resource='django.template.base.Template.render',
        meta={
            'django.template.engine.class': 'django.template.engine.Engine',
        },
    )
