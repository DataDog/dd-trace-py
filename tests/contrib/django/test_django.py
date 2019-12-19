import django
import pytest

from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.ext import http
from ddtrace.ext.priority import USER_KEEP
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID, HTTP_HEADER_PARENT_ID, HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.utils import get_wsgi_header

from tests.base import BaseTestCase
from ...util import assert_dict_issuperset


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason='')
def test_django_2XX_request_root_span(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.request` root span
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    spans = test_spans.get_spans()
    # Assert the correct number of traces and spans
    assert len(spans) == 26

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()

    if django.VERSION >= (2, 2, 0):
        resource = 'GET ^$'
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
        meta['http.route'] = '^$'

    root.assert_matches(
        name='django.request',
        service='django',
        resource=resource,
        parent_id=None,
        span_type=http.TYPE,
        error=0,
        meta=meta,
    )


@pytest.mark.skipif(django.VERSION >= (2, 0, 0), reason='')
def test_1XX_middleware(client, test_spans):
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    if django.VERSION < (1, 11, 0):
        test_spans.assert_span_count(15)
    else:
        test_spans.assert_span_count(16)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name='django.middleware'))
    if django.VERSION < (1, 11, 0):
        assert len(middleware_spans) == 13
    else:
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
        'django.contrib.sessions.middleware.SessionMiddleware.process_request',
        'django.middleware.common.CommonMiddleware.process_request',
        'django.middleware.csrf.CsrfViewMiddleware.process_request',  # Not in < 1.11.0
        'django.contrib.auth.middleware.AuthenticationMiddleware.process_request',
        'django.contrib.auth.middleware.SessionAuthenticationMiddleware.process_request',
        'django.contrib.messages.middleware.MessageMiddleware.process_request',
        'django.middleware.security.SecurityMiddleware.process_request',
        'django.middleware.csrf.CsrfViewMiddleware.process_view',
        'django.middleware.security.SecurityMiddleware.process_response',
        'django.middleware.clickjacking.XFrameOptionsMiddleware.process_response',
        'django.contrib.messages.middleware.MessageMiddleware.process_response',
        'django.middleware.csrf.CsrfViewMiddleware.process_response',
        'django.middleware.common.CommonMiddleware.process_response',
        'django.contrib.sessions.middleware.SessionMiddleware.process_response',
    ]
    if django.VERSION < (1, 11, 0):
        expected_resources.remove('django.middleware.csrf.CsrfViewMiddleware.process_request')
    middleware_spans = sorted(middleware_spans, key=lambda s: s.start)
    span_resources = [s.resource for s in middleware_spans]
    assert span_resources == expected_resources


@pytest.mark.skipif(django.VERSION < (2, 0, 0), reason='')
def test_2XX_middleware(client, test_spans):
    """
    When making a request to a Django app
        We properly create the `django.middleware` spans
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the correct number of traces and spans
    test_spans.assert_span_count(26)

    # Get all the `django.middleware` spans in this trace
    middleware_spans = list(test_spans.filter_spans(name='django.middleware'))
    for s in middleware_spans:
        print(s.resource)
    assert len(middleware_spans) == 24

    # Assert common span structure
    for span in middleware_spans:
        span.assert_matches(
            name='django.middleware',
            service='django',
            error=0,
            span_type=None,
        )

    span_resources = {
        'django.contrib.auth.middleware.AuthenticationMiddleware.__call__',
        'django.contrib.auth.middleware.AuthenticationMiddleware.process_request',
        'django.contrib.messages.middleware.MessageMiddleware.__call__',
        'django.contrib.messages.middleware.MessageMiddleware.process_request',
        'django.contrib.messages.middleware.MessageMiddleware.process_response',
        'django.contrib.sessions.middleware.SessionMiddleware.__call__',
        'django.contrib.sessions.middleware.SessionMiddleware.process_request',
        'django.contrib.sessions.middleware.SessionMiddleware.process_response',
        'django.middleware.clickjacking.XFrameOptionsMiddleware.__call__',
        'django.middleware.clickjacking.XFrameOptionsMiddleware.process_response',
        'django.middleware.common.CommonMiddleware.__call__',
        'django.middleware.common.CommonMiddleware.process_request',
        'django.middleware.common.CommonMiddleware.process_response',
        'django.middleware.csrf.CsrfViewMiddleware.__call__',
        'django.middleware.csrf.CsrfViewMiddleware.process_request',
        'django.middleware.csrf.CsrfViewMiddleware.process_response',
        'django.middleware.csrf.CsrfViewMiddleware.process_view',
        'django.middleware.security.SecurityMiddleware.__call__',
        'django.middleware.security.SecurityMiddleware.process_request',
        'django.middleware.security.SecurityMiddleware.process_response',
        'tests.contrib.django.middleware.ClsMiddleware.__call__',
        'tests.contrib.django.middleware.EverythingMiddleware',
        'tests.contrib.django.middleware.EverythingMiddleware.__call__',
        'tests.contrib.django.middleware.EverythingMiddleware.process_view'
    }
    assert set([s.resource for s in middleware_spans]) == span_resources

    # Get middleware spans in reverse order of start time
    middleware_spans = sorted(middleware_spans, key=lambda s: s.start, reverse=True)

    # Assert the first middleware span's parent is the root span (django.request)
    root_span = test_spans.get_root_span()
    assert root_span.name == 'django.request'
    first_middleware = middleware_spans[-1]
    assert first_middleware.parent_id == root_span.span_id


def test_request_view(client, test_spans):
    """
    When making a request to a Django app
        A `django.view` span is produced
    """
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

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


def test_django_request_not_found(client, test_spans):
    """
    When making a request to a Django app
        When the endpoint doesn't exist
            We create a 404 span
    """
    resp = client.get('/unknown/endpoint')
    assert resp.status_code == 404

    if django.VERSION >= (3, 0, 0):
        content = (
            b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Not Found</title>\n'
            b'</head>\n<body>\n  <h1>Not Found</h1><p>The requested resource was not found '
            b'on this server.</p>\n</body>\n</html>\n'
        )
    elif django.VERSION >= (1, 11, 0):
        content = b'<h1>Not Found</h1><p>The requested resource was not found on this server.</p>'
    else:
        content = b'<h1>Not Found</h1><p>The requested URL /unknown/endpoint was not found on this server.</p>'
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
    assert span.name == 'sqlite.query'
    assert span.service == 'defaultdb'
    assert span.span_type == 'sql'
    assert span.get_tag('django.db.vendor') == 'sqlite'
    assert span.get_tag('django.db.alias') == 'default'


"""
Caching tests
"""


def test_cache_get(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    cache.get('missing_key')

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == 'django'
    assert span.resource == 'django.core.cache.backends.locmem.get'
    assert span.name == 'django.cache'
    assert span.span_type == 'cache'
    assert span.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'missing_key',
    }

    assert_dict_issuperset(span.meta, expected_meta)


def test_cache_set(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    # (trace) the cache miss
    cache.set('a_new_key', 50)

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == 'django'
    assert span.resource == 'django.core.cache.backends.locmem.set'
    assert span.name == 'django.cache'
    assert span.span_type == 'cache'
    assert span.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'a_new_key',
    }

    assert_dict_issuperset(span.meta, expected_meta)


def test_cache_delete(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    cache.delete('an_existing_key')

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == 'django'
    assert span.resource == 'django.core.cache.backends.locmem.delete'
    assert span.name == 'django.cache'
    assert span.span_type == 'cache'
    assert span.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'an_existing_key',
    }

    assert_dict_issuperset(span.meta, expected_meta)


@pytest.mark.skipif(django.VERSION >= (2, 1, 0), reason='')
def test_cache_incr_1XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches['default']
    cache.set('value', 0)
    test_spans.tracer.writer.spans = []

    cache.incr('value')

    spans = test_spans.get_spans()
    assert len(spans) == 2

    span_incr = spans[0]
    span_get = spans[1]

    # LocMemCache doesn't provide an atomic operation
    assert span_get.service == 'django'
    assert span_get.resource == 'django.core.cache.backends.locmem.get'
    assert span_get.name == 'django.cache'
    assert span_get.span_type == 'cache'
    assert span_get.error == 0
    assert span_incr.service == 'django'
    assert span_incr.resource == 'django.core.cache.backends.locmem.incr'
    assert span_incr.name == 'django.cache'
    assert span_incr.span_type == 'cache'
    assert span_incr.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'value',
    }

    assert_dict_issuperset(span_get.meta, expected_meta)
    assert_dict_issuperset(span_incr.meta, expected_meta)


@pytest.mark.skipif(django.VERSION < (2, 1, 0), reason='')
def test_cache_incr_2XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches['default']
    cache.set('value', 0)
    test_spans.tracer.writer.spans = []

    cache.incr('value')

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span_incr = spans[0]

    # LocMemCache doesn't provide an atomic operation
    assert span_incr.service == 'django'
    assert span_incr.resource == 'django.core.cache.backends.locmem.incr'
    assert span_incr.name == 'django.cache'
    assert span_incr.span_type == 'cache'
    assert span_incr.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'value',
    }

    assert_dict_issuperset(span_incr.meta, expected_meta)


@pytest.mark.skipif(django.VERSION >= (2, 1, 0), reason='')
def test_cache_decr_1XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches['default']
    cache.set('value', 0)
    test_spans.tracer.writer.spans = []

    cache.decr('value')

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_decr = spans[0]
    span_incr = spans[1]
    span_get = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_get.service == 'django'
    assert span_get.resource == 'django.core.cache.backends.locmem.get'
    assert span_get.name == 'django.cache'
    assert span_get.span_type == 'cache'
    assert span_get.error == 0
    assert span_incr.service == 'django'
    assert span_incr.resource == 'django.core.cache.backends.locmem.incr'
    assert span_incr.name == 'django.cache'
    assert span_incr.span_type == 'cache'
    assert span_incr.error == 0
    assert span_decr.service == 'django'
    assert span_decr.resource == 'django.core.cache.backends.base.decr'
    assert span_decr.name == 'django.cache'
    assert span_decr.span_type == 'cache'
    assert span_decr.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'value',
    }

    assert_dict_issuperset(span_get.meta, expected_meta)
    assert_dict_issuperset(span_incr.meta, expected_meta)
    assert_dict_issuperset(span_decr.meta, expected_meta)


@pytest.mark.skipif(django.VERSION < (2, 1, 0), reason='')
def test_cache_decr_2XX(test_spans):
    # get the default cache, set the value and reset the spans
    cache = django.core.cache.caches['default']
    cache.set('value', 0)
    test_spans.tracer.writer.spans = []

    cache.decr('value')

    spans = test_spans.get_spans()
    assert len(spans) == 2

    span_decr = spans[0]
    span_incr = spans[1]

    # LocMemCache doesn't provide an atomic operation
    assert span_incr.service == 'django'
    assert span_incr.resource == 'django.core.cache.backends.locmem.incr'
    assert span_incr.name == 'django.cache'
    assert span_incr.span_type == 'cache'
    assert span_incr.error == 0
    assert span_decr.service == 'django'
    assert span_decr.resource == 'django.core.cache.backends.base.decr'
    assert span_decr.name == 'django.cache'
    assert span_decr.span_type == 'cache'
    assert span_decr.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'value',
    }

    assert_dict_issuperset(span_incr.meta, expected_meta)
    assert_dict_issuperset(span_decr.meta, expected_meta)


def test_cache_get_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    cache.get_many(['missing_key', 'another_key'])

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_get_many = spans[0]
    span_get_first = spans[1]
    span_get_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_get_first.service == 'django'
    assert span_get_first.resource == 'django.core.cache.backends.locmem.get'
    assert span_get_first.name == 'django.cache'
    assert span_get_first.span_type == 'cache'
    assert span_get_first.error == 0
    assert span_get_second.service == 'django'
    assert span_get_second.resource == 'django.core.cache.backends.locmem.get'
    assert span_get_second.name == 'django.cache'
    assert span_get_second.span_type == 'cache'
    assert span_get_second.error == 0
    assert span_get_many.service == 'django'
    assert span_get_many.resource == 'django.core.cache.backends.base.get_many'
    assert span_get_many.name == 'django.cache'
    assert span_get_many.span_type == 'cache'
    assert span_get_many.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': str(['missing_key', 'another_key']),
    }

    assert_dict_issuperset(span_get_many.meta, expected_meta)


def test_cache_set_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    cache.set_many({'first_key': 1, 'second_key': 2})

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_set_many = spans[0]
    span_set_first = spans[1]
    span_set_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_set_first.service == 'django'
    assert span_set_first.resource == 'django.core.cache.backends.locmem.set'
    assert span_set_first.name == 'django.cache'
    assert span_set_first.span_type == 'cache'
    assert span_set_first.error == 0
    assert span_set_second.service == 'django'
    assert span_set_second.resource == 'django.core.cache.backends.locmem.set'
    assert span_set_second.name == 'django.cache'
    assert span_set_second.span_type == 'cache'
    assert span_set_second.error == 0
    assert span_set_many.service == 'django'
    assert span_set_many.resource == 'django.core.cache.backends.base.set_many'
    assert span_set_many.name == 'django.cache'
    assert span_set_many.span_type == 'cache'
    assert span_set_many.error == 0

    assert span_set_many.meta['django.cache.backend'] == 'django.core.cache.backends.locmem.LocMemCache'
    assert 'first_key' in span_set_many.meta['django.cache.key']
    assert 'second_key' in span_set_many.meta['django.cache.key']


def test_cache_delete_many(test_spans):
    # get the default cache
    cache = django.core.cache.caches['default']

    cache.delete_many(['missing_key', 'another_key'])

    spans = test_spans.get_spans()
    assert len(spans) == 3

    span_delete_many = spans[0]
    span_delete_first = spans[1]
    span_delete_second = spans[2]

    # LocMemCache doesn't provide an atomic operation
    assert span_delete_first.service == 'django'
    assert span_delete_first.resource == 'django.core.cache.backends.locmem.delete'
    assert span_delete_first.name == 'django.cache'
    assert span_delete_first.span_type == 'cache'
    assert span_delete_first.error == 0
    assert span_delete_second.service == 'django'
    assert span_delete_second.resource == 'django.core.cache.backends.locmem.delete'
    assert span_delete_second.name == 'django.cache'
    assert span_delete_second.span_type == 'cache'
    assert span_delete_second.error == 0
    assert span_delete_many.service == 'django'
    assert span_delete_many.resource == 'django.core.cache.backends.base.delete_many'
    assert span_delete_many.name == 'django.cache'
    assert span_delete_many.span_type == 'cache'
    assert span_delete_many.error == 0

    assert span_delete_many.meta['django.cache.backend'] == 'django.core.cache.backends.locmem.LocMemCache'
    assert 'missing_key' in span_delete_many.meta['django.cache.key']
    assert 'another_key' in span_delete_many.meta['django.cache.key']


@pytest.mark.django_db
def test_cached_view(client, test_spans):
    # make the first request so that the view is cached
    response = client.get('/cached-users/')
    assert response.status_code == 200

    # check the first call for a non-cached view
    spans = list(test_spans.filter_spans(name='django.cache'))
    assert len(spans) == 3
    # the cache miss
    assert spans[0].resource == 'django.core.cache.backends.locmem.get'
    # store the result in the cache
    assert spans[1].resource == 'django.core.cache.backends.locmem.set'
    assert spans[2].resource == 'django.core.cache.backends.locmem.set'

    # check if the cache hit is traced
    response = client.get('/cached-users/')
    assert response.status_code == 200
    spans = list(test_spans.filter_spans(name='django.cache'))
    # There should be two more spans now
    assert len(spans) == 5

    span_header = spans[3]
    span_view = spans[4]
    assert span_view.service == 'django'
    assert span_view.resource == 'django.core.cache.backends.locmem.get'
    assert span_view.name == 'django.cache'
    assert span_view.span_type == 'cache'
    assert span_view.error == 0
    assert span_header.service == 'django'
    assert span_header.resource == 'django.core.cache.backends.locmem.get'
    assert span_header.name == 'django.cache'
    assert span_header.span_type == 'cache'
    assert span_header.error == 0

    expected_meta_view = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': (
            'views.decorators.cache.cache_page..'
            'GET.03cdc1cc4aab71b038a6764e5fcabb82.d41d8cd98f00b204e9800998ecf8427e.en-us'
        ),
    }

    expected_meta_header = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'views.decorators.cache.cache_header..03cdc1cc4aab71b038a6764e5fcabb82.en-us',
    }

    assert span_view.meta == expected_meta_view
    assert span_header.meta == expected_meta_header


@pytest.mark.django_db
def test_cached_template(client, test_spans):
    # make the first request so that the view is cached
    response = client.get('/cached-template/')
    assert response.status_code == 200

    # check the first call for a non-cached view
    spans = list(test_spans.filter_spans(name='django.cache'))
    assert len(spans) == 2
    # the cache miss
    assert spans[0].resource == 'django.core.cache.backends.locmem.get'
    # store the result in the cache
    assert spans[1].resource == 'django.core.cache.backends.locmem.set'

    # check if the cache hit is traced
    response = client.get('/cached-template/')
    assert response.status_code == 200
    spans = list(test_spans.filter_spans(name='django.cache'))
    # Should have 1 more span
    assert len(spans) == 3

    span_template_cache = spans[2]
    assert span_template_cache.service == 'django'
    assert span_template_cache.resource == 'django.core.cache.backends.locmem.get'
    assert span_template_cache.name == 'django.cache'
    assert span_template_cache.span_type == 'cache'
    assert span_template_cache.error == 0

    expected_meta = {
        'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
        'django.cache.key': 'template.cache.users_list.d41d8cd98f00b204e9800998ecf8427e',
    }

    assert span_template_cache.meta == expected_meta


"""
Configuration tests
"""


def test_service_can_be_overridden(client, test_spans):
    with BaseTestCase.override_config('django', dict(service_name='test-service')):
        response = client.get('/')
        assert response.status_code == 200

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == 'test-service'


@pytest.mark.django_db
def test_database_service_prefix_can_be_overridden(test_spans):
    with BaseTestCase.override_config('django', dict(database_service_name_prefix='my-')):
        from django.contrib.auth.models import User
        User.objects.count()

    spans = test_spans.get_spans()
    assert len(spans) > 0

    span = spans[0]
    assert span.service == 'my-defaultdb'


def test_cache_service_can_be_overridden(test_spans):
    cache = django.core.cache.caches['default']

    with BaseTestCase.override_config('django', dict(cache_service_name='test-cache-service')):
        cache.get('missing_key')

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.service == 'test-cache-service'


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

    # Assert that the trace properly inherits from the distributed headers
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


def test_django_request_distributed_disabled(client, test_spans):
    """
    When making a request to a Django app
        With distributed tracing headers
            When distributed tracing is disabled
                The django.request span doesn't inherit from the distributed trace
    """
    headers = {
        get_wsgi_header(HTTP_HEADER_TRACE_ID): '12345',
        get_wsgi_header(HTTP_HEADER_PARENT_ID): '78910',
        get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY): USER_KEEP,
    }
    with BaseTestCase.override_config('django', dict(distributed_tracing_enabled=False)):
        resp = client.get('/', **headers)
    assert resp.status_code == 200
    assert resp.content == b'Hello, test app.'

    # Assert the trace doesn't inherit from the distributed trace
    root = test_spans.find_span(name='django.request')
    assert root.trace_id != 12345
    assert root.parent_id is None


"""
Template tests
"""


def test_template(test_spans):
    # prepare a base template using the default engine
    template = django.template.Template('Hello {{name}}!')
    ctx = django.template.Context({'name': 'Django'})

    assert template.render(ctx) == 'Hello Django!'

    spans = test_spans.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.span_type == 'template'
    assert span.name == 'django.template.render'

    template.name = 'my-template'
    assert template.render(ctx) == 'Hello Django!'
    spans = test_spans.get_spans()
    assert len(spans) == 2

    span = spans[1]
    assert span.get_tag('django.template.name') == 'my-template'
