from ddtrace.ext import http


def assert_trace_count(test_spans):
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(10)


def test_django_request_root_span(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.request` root span
    """
    resp = client.get('/')
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    assert_trace_count(test_spans)

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()
    root.assert_matches(
        name='django.request',
        service='django',
        resource='GET ^/',
        parent_id=None,
        span_type=http.TYPE,
        error=0,
        meta={
            'django.request.class': 'django.core.handlers.wsgi.WSGIRequest',
            'django.response.class': 'django.http.response.HttpResponse',
            'django.view': 'tests.contrib.django.views.index',
            'http.method': 'GET',
            'http.route': '^/',
            'http.status_code': '200',
            'http.url': 'http://testserver/',
        }
    )


def test_django_request_middleware(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get('/')
    assert resp.content == b'Hello, test app.'

    # Assert rhe correct number of traces and spans
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
