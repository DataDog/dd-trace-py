import sqlalchemy

from unittest import TestCase

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
        dsn = 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG
        self.engine = sqlalchemy.create_engine(dsn)
        self.tracer = get_dummy_tracer()
        Pin.override(self.engine, tracer=self.tracer)

        # prepare a connection
        self.conn = self.engine.connect()

    def tearDown(self):
        # clear the database and dispose the engine
        self.conn.close()
        self.engine.dispose()
        unpatch()

    def test_engine_traced(self):
        # ensures that the engine is traced
        rows = self.conn.execute('SELECT 1').fetchall()
        assert len(rows) == 1

        traces = self.tracer.writer.pop_traces()
        # trace composition
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        # check subset of span fields
        assert span.name == 'postgres.query'
        assert span.service == 'postgres'
        assert span.error == 0
        assert span.duration > 0

    def test_engine_pin_service(self):
        # ensures that the engine service is updated with the PIN object
        Pin.override(self.engine, service='replica-db')
        rows = self.conn.execute('SELECT 1').fetchall()
        assert len(rows) == 1

        traces = self.tracer.writer.pop_traces()
        # trace composition
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        # check subset of span fields
        assert span.name == 'postgres.query'
        assert span.service == 'replica-db'
        assert span.error == 0
        assert span.duration > 0
