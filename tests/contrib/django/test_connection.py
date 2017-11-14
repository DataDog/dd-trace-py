import time

# 3rd party
from nose.tools import eq_
from django.contrib.auth.models import User

from ddtrace.contrib.django.conf import settings

# testing
from .utils import DjangoTraceTestCase, override_ddtrace_settings


class DjangoConnectionTest(DjangoTraceTestCase):
    """
    Ensures that database connections are properly traced
    """
    def test_connection(self):
        # trace a simple query
        start = time.time()
        users = User.objects.count()
        eq_(users, 0)
        end = time.time()

        # tests
        spans = self.tracer.writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        span = spans[0]
        eq_(span.name, 'sqlite.query')
        eq_(span.service, 'defaultdb')
        eq_(span.span_type, 'sql')
        eq_(span.get_tag('django.db.vendor'), 'sqlite')
        eq_(span.get_tag('django.db.alias'), 'default')
        eq_(span.get_tag('sql.query'), 'SELECT COUNT(*) AS "__count" FROM "auth_user"')
        assert start < span.start < span.start + span.duration < end

    @override_ddtrace_settings(INSTRUMENT_DATABASE=False)
    def test_connection_disabled(self):
        # trace a simple query
        users = User.objects.count()
        eq_(users, 0)

        # tests
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def test_should_append_database_prefix(self):
        # trace a simple query and check if the prefix is correctly
        # loaded from Django settings
        settings.DEFAULT_DATABASE_PREFIX = 'my_prefix_db'
        User.objects.count()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.service, 'my_prefix_db-defaultdb')
