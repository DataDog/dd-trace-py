
import sqlite3
import time

from nose.tools import eq_

from ... import Tracer
from ...contrib.sqlite3 import connection_factory
from ...test_tracer import DummyWriter
from ...ext import errors

def test_foo():
    writer = DummyWriter()
    tracer = Tracer(writer=writer)

    # ensure we can trace multiple services without stomping

    services = ["db", "another"]
    for service in services:
        conn_factory = connection_factory(tracer, service=service)
        db = sqlite3.connect(":memory:", factory=conn_factory)

        # Ensure we can run a query and it's correctly traced
        q = "select * from sqlite_master"
        start = time.time()
        cursor = db.execute(q)
        rows = cursor.fetchall()
        end = time.time()
        assert not rows
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, "sqlite3.query")
        eq_(span.span_type, "sql")
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta["sql.query"], q)
        eq_(span.error, 0)
        assert start <= span.start <= end
        assert span.duration <= end - start

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        try:
            db.execute(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, "sqlite3.query")
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta["sql.query"], q)
        eq_(span.error, 1)
        eq_(span.span_type, "sql")
        assert span.get_tag(errors.ERROR_STACK)
        assert 'OperationalError' in span.get_tag(errors.ERROR_TYPE)
        assert 'no such table' in span.get_tag(errors.ERROR_MSG)


