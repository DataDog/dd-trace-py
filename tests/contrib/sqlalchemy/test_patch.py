import sqlalchemy

from unittest import TestCase
from nose.tools import eq_, ok_

from ddtrace import Pin
from ddtrace.contrib.sqlalchemy import patch, unpatch

from ..config import POSTGRES_CONFIG
from ...test_tracer import get_dummy_tracer


class SQLAlchemyPatchTestCase(TestCase):
    """TestCase that checks if the engine is properly traced
    when the `patch()` method is used.
    """
    def setUp(self):
        # create a traced engine with the given arguments
        # and configure the current PIN instance
        patch()
        self.dsn = 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG
        self.engine = sqlalchemy.create_engine(self.dsn)
        self.tracer = get_dummy_tracer()
        Pin.override(self.engine, tracer=self.tracer)

        # prepare a connection
        self.conn = self.engine.connect()

    def tearDown(self):
        # clear the database and dispose the engine
        self.conn.close()
        self.engine.dispose()
        unpatch()

    def test_unpatch(self):
        unpatch()
        engine = sqlalchemy.create_engine(self.dsn)
        Pin.override(engine, tracer=self.tracer)

        # prepare a connection
        conn = engine.connect()
        conn.execute('SELECT 1').fetchall()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    def test_double_patch(self):
        # setUp() already patches, patch again and make sure we're idempotent
        patch()
        self.conn.execute('SELECT 1').fetchall()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    def test_double_unpatch_patch(self):
        # setUp() already patches, unpatch
        unpatch()
        unpatch()
        patch()
        self.conn.execute('SELECT 1').fetchall()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    def test_engine_traced(self):
        # ensures that the engine is traced
        rows = self.conn.execute('SELECT 1').fetchall()
        eq_(len(rows), 1)

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # check subset of span fields
        eq_(span.name, 'postgres.query')
        eq_(span.service, 'postgres')
        eq_(span.error, 0)
        ok_(span.duration > 0)

    def test_engine_pin_service(self):
        # ensures that the engine service is updated with the PIN object
        Pin.override(self.engine, service='replica-db')
        rows = self.conn.execute('SELECT 1').fetchall()
        eq_(len(rows), 1)

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # check subset of span fields
        eq_(span.name, 'postgres.query')
        eq_(span.service, 'replica-db')
        eq_(span.error, 0)
        ok_(span.duration > 0)
