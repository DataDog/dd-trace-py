import time

# 3rd party
from nose.tools import eq_, ok_

from django.core.urlresolvers import reverse

# testing
from .utils import DjangoTraceTestCase


class DjangoCacheViewTest(DjangoTraceTestCase):
    """
    Ensures that the cache system is properly traced
    """
    def test_cached_view(self):
        # make the first request so that the view is cached
        url = reverse('cached-users-list')
        response = self.client.get(url)
        eq_(response.status_code, 200)

        # check the first call for a non-cached view
        spans = self.tracer.writer.pop()
        eq_(len(spans), 6)
        # the cache miss
        eq_(spans[0].resource, 'get')
        # store the result in the cache
        eq_(spans[3].resource, 'set')
        eq_(spans[4].resource, 'set')

        # check if the cache hit is traced
        response = self.client.get(url)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_header = spans[0]
        span_view = spans[1]
        eq_(span_view.service, 'django')
        eq_(span_view.resource, 'get')
        eq_(span_view.name, 'django.cache')
        eq_(span_view.span_type, 'cache')
        eq_(span_view.error, 0)
        eq_(span_header.service, 'django')
        eq_(span_header.resource, 'get')
        eq_(span_header.name, 'django.cache')
        eq_(span_header.span_type, 'cache')
        eq_(span_header.error, 0)

        expected_meta_view = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'views.decorators.cache.cache_page..GET.03cdc1cc4aab71b038a6764e5fcabb82.d41d8cd98f00b204e9800998ecf8427e.en-us',
            'env': 'test',
        }

        expected_meta_header = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'views.decorators.cache.cache_header..03cdc1cc4aab71b038a6764e5fcabb82.en-us',
            'env': 'test',
        }

        eq_(span_view.meta, expected_meta_view)
        eq_(span_header.meta, expected_meta_header)

    def test_cached_template(self):
        # make the first request so that the view is cached
        url = reverse('cached-template-list')
        response = self.client.get(url)
        eq_(response.status_code, 200)

        # check the first call for a non-cached view
        spans = self.tracer.writer.pop()
        eq_(len(spans), 5)
        # the cache miss
        eq_(spans[0].resource, 'get')
        # store the result in the cache
        eq_(spans[2].resource, 'set')

        # check if the cache hit is traced
        response = self.client.get(url)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)

        span_template_cache = spans[0]
        eq_(span_template_cache.service, 'django')
        eq_(span_template_cache.resource, 'get')
        eq_(span_template_cache.name, 'django.cache')
        eq_(span_template_cache.span_type, 'cache')
        eq_(span_template_cache.error, 0)

        expected_meta = {
            'django.cache.backend': 'django.core.cache.backends.locmem.LocMemCache',
            'django.cache.key': 'template.cache.users_list.d41d8cd98f00b204e9800998ecf8427e',
            'env': 'test',
        }

        eq_(span_template_cache.meta, expected_meta)
