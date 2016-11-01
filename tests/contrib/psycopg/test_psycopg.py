# stdlib
import time

# 3p
import psycopg2
from nose.tools import eq_

# project
from ddtrace import Tracer
from ddtrace.contrib.psycopg import connection_factory

# testing
from ..config import POSTGRES_CONFIG
from ...test_tracer import DummyWriter


TEST_PORT = str(POSTGRES_CONFIG['port'])

def assert_conn_is_traced(tracer, db, service):

    # ensure the trace pscyopg client doesn't add non-standard
    # methods
    try:
        db.execute("select 'foobar'")
    except AttributeError:
        pass

    writer = tracer.writer
    # Ensure we can run a query and it's correctly traced
    q = "select 'foobarblah'"
    start = time.time()
    cursor = db.cursor()
    cursor.execute(q)
    rows = cursor.fetchall()
    end = time.time()
    eq_(rows, [('foobarblah',)])
    assert rows
    spans = writer.pop()
    assert spans
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.name, "postgres.query")
    eq_(span.resource, q)
    eq_(span.service, service)
    eq_(span.meta["sql.query"], q)
    eq_(span.error, 0)
    eq_(span.span_type, "sql")
    assert start <= span.start <= end
    assert span.duration <= end - start

    # run a query with an error and ensure all is well
    q = "select * from some_non_existant_table"
    cur = db.cursor()
    try:
        cur.execute(q)
    except Exception:
        pass
    else:
        assert 0, "should have an error"
    spans = writer.pop()
    assert spans, spans
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.name, "postgres.query")
    eq_(span.resource, q)
    eq_(span.service, service)
    eq_(span.meta["sql.query"], q)
    eq_(span.error, 1)
    eq_(span.meta["out.host"], "localhost")
    eq_(span.meta["out.port"], TEST_PORT)
    eq_(span.span_type, "sql")

def test_manual_wrap():
    from ddtrace.contrib.psycopg.patch import wrap
    db = psycopg2.connect(**POSTGRES_CONFIG)

    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
    wrapped = wrap(db, service="foo", tracer=tracer)
    assert_conn_is_traced(tracer, wrapped, "foo")



def test_connect_factory():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    services = ["db", "another"]
    for service in services:
        conn_factory = connection_factory(tracer, service=service)
        db = psycopg2.connect(connection_factory=conn_factory, **POSTGRES_CONFIG)
        assert_conn_is_traced(tracer, db, service)

    # ensure we have the service types
    services = writer.pop_services()
    expected = {
        "db" : {"app":"postgres", "app_type":"db"},
        "another" : {"app":"postgres", "app_type":"db"},
    }
    eq_(services, expected)
