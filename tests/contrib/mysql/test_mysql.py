# 3p
import mysql
from nose.tools import eq_, ok_

# project
from ddtrace import Pin
from ddtrace.contrib.mysql.patch import patch, unpatch

# tests
from tests.contrib.config import MYSQL_CONFIG
from tests.opentracer.utils import init_tracer
from tests.test_tracer import get_dummy_tracer
from ...util import assert_dict_issuperset


class MySQLCore(object):
    """Base test case for MySQL drivers"""
    conn = None
    TEST_SERVICE = 'test-mysql'

    def tearDown(self):
        # Reuse the connection across tests
        if self.conn:
            try:
                self.conn.ping()
            except MySQLdb.InterfaceError:
                pass
            else:
                self.conn.close()
        unpatch()

    def _get_conn_tracer(self):
        # implement me
        pass

    def test_simple_query(self):
        conn, tracer = self._get_conn_tracer()
        writer = tracer.writer
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        eq_(len(rows), 1)
        spans = writer.pop()
        eq_(len(spans), 2)

        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'mysql.query')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        assert_dict_issuperset(span.meta, {
            'out.host': u'127.0.0.1',
            'out.port': u'3306',
            'db.name': u'test',
            'db.user': u'test',
        })

        eq_(spans[1].name, 'mysql.query.fetchall')

    def test_query_with_several_rows(self):
        conn, tracer = self._get_conn_tracer()
        writer = tracer.writer
        cursor = conn.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        eq_(len(rows), 3)
        spans = writer.pop()
        eq_(len(spans), 2)
        span = spans[0]
        ok_(span.get_tag('sql.query') is None)
        eq_(spans[1].name, 'mysql.query.fetchall')

    def test_query_many(self):
        # tests that the executemany method is correctly wrapped.
        conn, tracer = self._get_conn_tracer()
        writer = tracer.writer
        tracer.enabled = False
        cursor = conn.cursor()

        cursor.execute("""
            create table if not exists dummy (
                dummy_key VARCHAR(32) PRIMARY KEY,
                dummy_value TEXT NOT NULL)""")
        tracer.enabled = True

        stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
        data = [("foo","this is foo"),
                ("bar","this is bar")]
        cursor.executemany(stmt, data)
        query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
        cursor.execute(query)
        rows = cursor.fetchall()
        eq_(len(rows), 2)
        eq_(rows[0][0], "bar")
        eq_(rows[0][1], "this is bar")
        eq_(rows[1][0], "foo")
        eq_(rows[1][1], "this is foo")

        spans = writer.pop()
        eq_(len(spans), 3)
        span = spans[-1]
        ok_(span.get_tag('sql.query') is None)
        cursor.execute("drop table if exists dummy")

        eq_(spans[2].name, 'mysql.query.fetchall')

    def test_query_proc(self):
        conn, tracer = self._get_conn_tracer()
        writer = tracer.writer

        # create a procedure
        tracer.enabled = False
        cursor = conn.cursor()
        cursor.execute("DROP PROCEDURE IF EXISTS sp_sum")
        cursor.execute("""
            CREATE PROCEDURE sp_sum (IN p1 INTEGER, IN p2 INTEGER, OUT p3 INTEGER)
            BEGIN
                SET p3 := p1 + p2;
            END;""")

        tracer.enabled = True
        proc = "sp_sum"
        data = (40, 2, None)
        output = cursor.callproc(proc, data)
        eq_(len(output), 3)
        eq_(output[2], 42)

        spans = writer.pop()
        assert spans, spans

        # number of spans depends on MySQL implementation details,
        # typically, internal calls to execute, but at least we
        # can expect the last closed span to be our proc.
        span = spans[len(spans) - 1]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'mysql.query')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        assert_dict_issuperset(span.meta, {
            'out.host': u'127.0.0.1',
            'out.port': u'3306',
            'db.name': u'test',
            'db.user': u'test',
        })
        ok_(span.get_tag('sql.query') is None)

    def test_simple_query_ot(self):
        """OpenTracing version of test_simple_query."""
        conn, tracer = self._get_conn_tracer()
        writer = tracer.writer

        ot_tracer = init_tracer('mysql_svc', tracer)

        with ot_tracer.start_active_span('mysql_op'):
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            eq_(len(rows), 1)

        spans = writer.pop()
        eq_(len(spans), 3)

        ot_span, dd_span, fetch_span = spans

        # confirm parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.service, 'mysql_svc')
        eq_(ot_span.name, 'mysql_op')

        eq_(dd_span.service, self.TEST_SERVICE)
        eq_(dd_span.name, 'mysql.query')
        eq_(dd_span.span_type, 'sql')
        eq_(dd_span.error, 0)
        assert_dict_issuperset(dd_span.meta, {
            'out.host': u'127.0.0.1',
            'out.port': u'3306',
            'db.name': u'test',
            'db.user': u'test',
        })

        eq_(fetch_span.name, 'mysql.query.fetchall')

class TestMysqlPatch(MySQLCore):

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()
        MySQLCore.tearDown(self)

    def _get_conn_tracer(self):
        if not self.conn:
            tracer = get_dummy_tracer()
            self.conn = mysql.connector.connect(**MYSQL_CONFIG)
            assert self.conn.is_connected()
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            assert pin.service == 'mysql'
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin.clone(
                service=self.TEST_SERVICE, tracer=tracer).onto(self.conn)

            return self.conn, tracer

    def test_patch_unpatch(self):
        unpatch()
        # assert we start unpatched
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        assert not Pin.get_from(conn)
        conn.close()

        patch()
        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            pin = Pin.get_from(conn)
            assert pin
            pin.clone(
                service=self.TEST_SERVICE, tracer=tracer).onto(conn)
            assert conn.is_connected()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            eq_(len(rows), 1)
            spans = writer.pop()
            eq_(len(spans), 2)

            span = spans[0]
            eq_(span.service, self.TEST_SERVICE)
            eq_(span.name, 'mysql.query')
            eq_(span.span_type, 'sql')
            eq_(span.error, 0)
            assert_dict_issuperset(span.meta, {
                'out.host': u'127.0.0.1',
                'out.port': u'3306',
                'db.name': u'test',
                'db.user': u'test',
            })
            ok_(span.get_tag('sql.query') is None)

            eq_(spans[1].name, 'mysql.query.fetchall')

        finally:
            unpatch()

            # assert we finish unpatched
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            assert not Pin.get_from(conn)
            conn.close()

        patch()
