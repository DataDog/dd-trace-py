import time

import psycopg2
from nose.tools import eq_
from nose.plugins.skip import SkipTest

from tracer import Tracer
from tracer.contrib.psycopg import connection_factory
from tracer.test_tracer import DummyWriter

def test_wrap():
    raise SkipTest("matt")

    writer = DummyWriter()
    tracer = Tracer(writer=writer)

    params = {
        'host' : 'localhost',
        'port' : 5432,
        'user' : 'dog',
        'password' :'dog',
        'dbname' : 'dogdata',
    }

    services = ["db", "another"]
    for service in services:
        conn_factory = connection_factory(tracer, service=service)
        db = psycopg2.connect(connection_factory=conn_factory, **params)

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
