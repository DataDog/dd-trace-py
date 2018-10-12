# stdlib
import sqlite3
import time

# 3p
from nose.tools import eq_, ok_

# project
import ddtrace
from ddtrace import Pin
from ddtrace.contrib.sqlite3 import connection_factory
from ddtrace.contrib.sqlite3.patch import patch, unpatch
from ddtrace.ext import errors

# testing
from tests.opentracer.utils import init_tracer
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

    def test_service_info(self):
        tracer = get_dummy_tracer()
        backup_tracer = ddtrace.tracer
        ddtrace.tracer = tracer

        db = sqlite3.connect(":memory:")

        services = tracer.writer.pop_services()
        eq_(len(services), 1)
        expected = {
            'sqlite': {'app': 'sqlite', 'app_type': 'db'}
        }
        eq_(expected, services)

        ddtrace.tracer = backup_tracer

    def test_sqlite(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # ensure we can trace multiple services without stomping
        services = ["db", "another"]
        for service in services:
            db = sqlite3.connect(":memory:")
            pin = Pin.get_from(db)
            assert pin
            eq_("db", pin.app_type)
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
            ok_(span.get_tag("sql.query") is None)
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
            ok_(span.get_tag("sql.query") is None)
            eq_(span.error, 1)
            eq_(span.span_type, "sql")
            assert span.get_tag(errors.ERROR_STACK)
            assert 'OperationalError' in span.get_tag(errors.ERROR_TYPE)
            assert 'no such table' in span.get_tag(errors.ERROR_MSG)

    def test_sqlite_ot(self):
        """Ensure sqlite works with the opentracer."""
        tracer = get_dummy_tracer()
        ot_tracer = init_tracer('sqlite_svc', tracer)

        # Ensure we can run a query and it's correctly traced
        q = "select * from sqlite_master"
        with ot_tracer.start_active_span('sqlite_op'):
            db = sqlite3.connect(":memory:")
            pin = Pin.get_from(db)
            assert pin
            eq_("db", pin.app_type)
            pin.clone(tracer=tracer).onto(db)
            cursor = db.execute(q)
            rows = cursor.fetchall()
        assert not rows
        spans = tracer.writer.pop()
        assert spans

        print(spans)
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.name, 'sqlite_op')
        eq_(ot_span.service, 'sqlite_svc')

        eq_(dd_span.name, "sqlite.query")
        eq_(dd_span.span_type, "sql")
        eq_(dd_span.resource, q)
        ok_(dd_span.get_tag("sql.query") is None)
        eq_(dd_span.error, 0)

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

