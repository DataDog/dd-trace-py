
# stdlib
import sqlite3
import time

# 3p
from nose.tools import eq_

# project
from ddtrace import Tracer, Pin
from ddtrace.contrib.sqlite3.patch import patch_conn
from ddtrace.contrib.sqlite3 import connection_factory
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

def test_sqlite():
    tracer = get_dummy_tracer()
    writer = tracer.writer

    # ensure we can trace multiple services without stomping
    services = ["db", "another"]
    for service in services:
        db = patch_conn(sqlite3.connect(":memory:"))
        pin = Pin.get_from(db)
        assert pin
        pin.service = service
        pin.tracer = tracer
        pin.onto(db)

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

    # # ensure we have the service types
    # services = writer.pop_services()
    # expected = {
    #     "db" : {"app":"sqlite", "app_type":"db"},
    #     "another" : {"app":"sqlite", "app_type":"db"},
    # }
    # eq_(services, expected)


