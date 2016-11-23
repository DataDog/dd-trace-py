
# stdlib
import sqlite3
import time

# 3p
from nose.tools import eq_

# project
from ddtrace import Pin
from ddtrace.contrib.sqlite3 import connection_factory
from ddtrace.contrib.sqlite3.patch import patch, unpatch
from ddtrace.ext import errors
from tests.test_tracer import get_dummy_tracer




def test_backwards_compat():
    # a small test to ensure that if the previous interface is used
    # things still work
    tracer = get_dummy_tracer()
    factory = connection_factory(tracer, service="my_db_service")
    conn = sqlite3.connect(":memory:", factory=factory)
    q = "select * from sqlite_master"
    rows = conn.execute(q)
    assert not rows.fetchall()
    assert not tracer.writer.pop()

class TestSQLite(object):
    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def test_sqlite(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # ensure we can trace multiple services without stomping
        services = ["db", "another"]
        for service in services:
            db = sqlite3.connect(":memory:")
            pin = Pin.get_from(db)
            assert pin
            pin.clone(
                service=service,
                tracer=tracer).onto(db)

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
            eq_(span.name, "sqlite.query")
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
            eq_(span.name, "sqlite.query")
            eq_(span.resource, q)
            eq_(span.service, service)
            eq_(span.meta["sql.query"], q)
            eq_(span.error, 1)
            eq_(span.span_type, "sql")
            assert span.get_tag(errors.ERROR_STACK)
            assert 'OperationalError' in span.get_tag(errors.ERROR_TYPE)
            assert 'no such table' in span.get_tag(errors.ERROR_MSG)

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        db = sqlite3.connect(":memory:")
        pin = Pin.get_from(db)
        assert pin 
        pin.clone(tracer=tracer).onto(db)
        db.cursor().execute("select 'blah'").fetchall()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        db = sqlite3.connect(":memory:")
        db.cursor().execute("select 'blah'").fetchall()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        db = sqlite3.connect(":memory:")
        pin = Pin.get_from(db)
        assert pin 
        pin.clone(tracer=tracer).onto(db)
        db.cursor().execute("select 'blah'").fetchall()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

