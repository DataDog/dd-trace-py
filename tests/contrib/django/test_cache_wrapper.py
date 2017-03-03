# 3rd party
from nose.tools import eq_, ok_, assert_raises
from django.core.cache import caches

# testing
from .utils import DjangoTraceTestCase


class DjangoCacheTest(DjangoTraceTestCase):
    """
    Ensures that the tracing doesn't break the Django
    cache framework
    """
    def test_wrapper_get_and_set(self):
        # get the default cache
        cache = caches['default']

        value = cache.get('missing_key')
        eq_(value, None)

        cache.set('a_key', 50)
        value = cache.get('a_key')
        eq_(value, 50)

    def test_wrapper_add(self):
        # get the default cache
        cache = caches['default']

        cache.add('a_key', 50)
        value = cache.get('a_key')
        eq_(value, 50)

        # add should not update a key if it's present
        cache.add('a_key', 40)
        value = cache.get('a_key')
        eq_(value, 50)

    def test_wrapper_delete(self):
        # get the default cache
        cache = caches['default']

        cache.set('a_key', 50)
        cache.delete('a_key')
        value = cache.get('a_key')
        eq_(value, None)

    def test_wrapper_incr_safety(self):
        # get the default cache
        cache = caches['default']

        # it should fail not because of our wrapper
        with assert_raises(ValueError) as ex:
            cache.incr('missing_key')

        # the error is not caused by our tracer
        eq_(ex.exception.args[0], "Key 'missing_key' not found")
        # an error trace must be sent
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        span = spans[0]
        eq_(span.resource, 'incr')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 1)

    def test_wrapper_incr(self):
        # get the default cache
        cache = caches['default']

        cache.set('value', 0)
        value = cache.incr('value')
        eq_(value, 1)
        value = cache.get('value')
        eq_(value, 1)

    def test_wrapper_decr_safety(self):
        # get the default cache
        cache = caches['default']

        # it should fail not because of our wrapper
        with assert_raises(ValueError) as ex:
            cache.decr('missing_key')

        # the error is not caused by our tracer
        eq_(ex.exception.args[0], "Key 'missing_key' not found")
        # an error trace must be sent
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.resource, 'decr')
        eq_(span.name, 'django.cache')
        eq_(span.span_type, 'cache')
        eq_(span.error, 1)

    def test_wrapper_decr(self):
        # get the default cache
        cache = caches['default']

        cache.set('value', 0)
        value = cache.decr('value')
        eq_(value, -1)
        value = cache.get('value')
        eq_(value, -1)

    def test_wrapper_get_many(self):
        # get the default cache
        cache = caches['default']

        cache.set('a_key', 50)
        cache.set('another_key', 60)

        values = cache.get_many(['a_key', 'another_key'])
        ok_(isinstance(values, dict))
        eq_(values['a_key'], 50)
        eq_(values['another_key'], 60)

    def test_wrapper_set_many(self):
        # get the default cache
        cache = caches['default']

        cache.set_many({'a_key': 50, 'another_key': 60})
        eq_(cache.get('a_key'), 50)
        eq_(cache.get('another_key'), 60)

    def test_wrapper_delete_many(self):
        # get the default cache
        cache = caches['default']

        cache.set('a_key', 50)
        cache.set('another_key', 60)
        cache.delete_many(['a_key', 'another_key'])
        eq_(cache.get('a_key'), None)
        eq_(cache.get('another_key'), None)
