#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from ddtrace.contrib.mysql import missing_modules

from nose.tools import eq_, assert_greater_equal, assert_is_not_none

from ddtrace.tracer import Tracer
from ddtrace.contrib.mysql import get_traced_mysql_connection

from tests.test_tracer import DummyWriter
from tests.contrib.config import MYSQL_CONFIG

META_KEY = "this.is"
META_VALUE = "A simple test value"
CREATE_TABLE_DUMMY = "CREATE TABLE IF NOT EXISTS dummy " \
                     "( dummy_key VARCHAR(32) PRIMARY KEY, " \
                     "dummy_value TEXT NOT NULL)"
DROP_TABLE_DUMMY = "DROP TABLE IF EXISTS dummy"
CREATE_PROC_SUM = "CREATE PROCEDURE\n" \
                     "sp_sum (IN p1 INTEGER, IN p2 INTEGER,\n" \
                     "OUT p3 INTEGER)\n" \
                     "BEGIN\n" \
                     "  SET p3 := p1 + p2;\n" \
                     "END;"
DROP_PROC_SUM = "DROP PROCEDURE IF EXISTS sp_sum"

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

class MySQLTest(unittest.TestCase):
    SERVICE = 'test-db'

    def tearDown(self):
        # FIXME: get rid of jumbo try/finally and
        # let this tearDown close all connections
        if hasattr(self, "conn") and self.conn:
            self.conn.close()

    def test_connection(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        assert_is_not_none(conn)
        conn.close()

    def test_simple_query(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer,
                                            service=MySQLTest.SERVICE,
                                            meta={META_KEY: META_VALUE})
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            eq_(len(rows), 1)
            spans = writer.pop()
            eq_(len(spans), 1)

            span = spans[0]
            eq_(span.service, self.SERVICE)
            eq_(span.name, 'mysql.execute')
            eq_(span.span_type, 'sql')
            eq_(span.error, 0)
            eq_(span.meta, {
                'out.host': u'127.0.0.1',
                'out.port': u'53306',
                'db.name': u'test',
                'db.user': u'test',
                'sql.query': u'SELECT 1',
                META_KEY: META_VALUE,
            })
            eq_(span.get_metric('sql.rows'), -1)
        finally:
            conn.close()

    def test_simple_fetch(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer,
                                            service=MySQLTest.SERVICE,
                                            meta={META_KEY: META_VALUE},
                                            trace_fetch=True)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            eq_(len(rows), 1)
            spans = writer.pop()
            eq_(len(spans), 2)

            span = spans[0]
            eq_(span.service, self.SERVICE)
            eq_(span.name, 'mysql.execute')
            eq_(span.span_type, 'sql')
            eq_(span.error, 0)
            eq_(span.meta, {
                'out.host': u'127.0.0.1',
                'out.port': u'53306',
                'db.name': u'test',
                'db.user': u'test',
                'sql.query': u'SELECT 1',
                META_KEY: META_VALUE,
            })
            eq_(span.get_metric('sql.rows'), -1)

            span = spans[1]
            eq_(span.service, self.SERVICE)
            eq_(span.name, 'mysql.fetchall')
            eq_(span.span_type, 'sql')
            eq_(span.error, 0)
            eq_(span.meta, {
                'out.host': u'127.0.0.1',
                'out.port': u'53306',
                'db.name': u'test',
                'db.user': u'test',
                'sql.query': u'SELECT 1',
                META_KEY: META_VALUE,
            })
            eq_(span.get_metric('sql.rows'), 1)
        finally:
            conn.close()

    def test_query_with_several_rows(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT n FROM "
                       "(SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m")
            rows = cursor.fetchall()
            eq_(len(rows), 3)
        finally:
            conn.close()

    def test_query_many(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()
            cursor.execute(CREATE_TABLE_DUMMY)
            stmt = "INSERT INTO dummy (dummy_key,dummy_value) VALUES (%s, %s)"
            data = [("foo","this is foo"),
                ("bar","this is bar")]
            cursor.executemany(stmt, data)
            cursor.execute("SELECT dummy_key, dummy_value FROM dummy "
                       "ORDER BY dummy_key")
            rows = cursor.fetchall()
            eq_(len(rows), 2)
            eq_(rows[0][0], "bar")
            eq_(rows[0][1], "this is bar")
            eq_(rows[1][0], "foo")
            eq_(rows[1][1], "this is foo")
            cursor.execute(DROP_TABLE_DUMMY)
        finally:
            conn.close()

    def test_query_proc(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()
            cursor.execute(DROP_PROC_SUM)
            cursor.execute(CREATE_PROC_SUM)
            proc = "sp_sum"
            data = (40, 2, None)
            output = cursor.callproc(proc, data)
            eq_(len(output), 3)
            eq_(output[2], 42)

            spans = writer.pop()

            # number of spans depends on MySQL implementation details,
            # typically, internal calls to execute, but at least we
            # can expect the last closed span to be our proc.
            span = spans[len(spans) - 1]
            eq_(span.service, self.SERVICE)
            eq_(span.name, 'mysql.callproc')
            eq_(span.span_type, 'sql')
            eq_(span.error, 0)
            eq_(span.meta, {
                'out.host': u'127.0.0.1',
                'out.port': u'53306',
                'db.name': u'test',
                'db.user': u'test',
                'sql.query': u'sp_sum',
            })
            eq_(span.get_metric('sql.rows'), 1)

            cursor.execute(DROP_PROC_SUM)
        finally:
            conn.close()

    def test_cursor_buffered_raw(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            for buffered in (None, False, True):
                for raw in (None, False, True):
                    cursor = conn.cursor(buffered=buffered, raw=raw)
                    if buffered:
                        if raw:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorBufferedRaw")
                        else:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorBuffered")
                    else:
                        if raw:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorRaw")
                        else:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursor")
                    cursor.execute("SELECT 1")
                    rows = cursor.fetchall()
                    eq_(len(rows), 1)
        finally:
            conn.close()

    def test_connection_buffered_raw(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        for buffered in (None, False, True):
            for raw in (None, False, True):
                conn = MySQL(buffered=buffered, raw=raw, **MYSQL_CONFIG)
                try:
                    cursor = conn.cursor()
                    if buffered:
                        if raw:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorBufferedRaw")
                        else:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorBuffered")
                    else:
                        if raw:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursorRaw")
                        else:
                            eq_(cursor._datadog_baseclass_name, "MySQLCursor")
                            cursor.execute("SELECT 1")
                            rows = cursor.fetchall()
                            eq_(len(rows), 1)
                finally:
                    conn.close()

    def test_fetch_variants(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer,
                                            service=MySQLTest.SERVICE,
                                            trace_fetch=True)
        conn = MySQL(**MYSQL_CONFIG)
        try:
            cursor = conn.cursor()

            cursor.execute(CREATE_TABLE_DUMMY)

            NB_FETCH_TOTAL = 30
            NB_FETCH_MANY = 5
            stmt = "INSERT INTO dummy (dummy_key,dummy_value) VALUES (%s, %s)"
            data = [("%02d" % i, "this is %d" % i) for i in range(NB_FETCH_TOTAL)]
            cursor.executemany(stmt, data)
            cursor.execute("SELECT dummy_key, dummy_value FROM dummy "
                           "ORDER BY dummy_key")

            rows = cursor.fetchmany(size=NB_FETCH_MANY)
            fetchmany_rowcount_a = cursor.rowcount
            fetchmany_nbrows_a = len(rows)
            eq_(fetchmany_rowcount_a, NB_FETCH_MANY)
            eq_(fetchmany_nbrows_a, NB_FETCH_MANY)

            rows = cursor.fetchone()
            fetchone_rowcount_a = cursor.rowcount
            eq_(fetchone_rowcount_a, NB_FETCH_MANY + 1)
            # carefull: rows contains only one line with the values,
            # not an array of lines, so since we're SELECTing 2 columns
            # (dummy_key, dummy_value) we get len()==2.
            eq_(len(rows), 2)

            rows = cursor.fetchone()
            fetchone_rowcount_a = cursor.rowcount
            eq_(fetchone_rowcount_a, NB_FETCH_MANY + 2)
            eq_(len(rows), 2)

            # Todo: check what happens when using fetchall(),
            # on some tests a line was missing when calling fetchall()
            # after fetchone().
            rows = cursor.fetchmany(size=NB_FETCH_TOTAL)
            fetchmany_rowcount_b = cursor.rowcount
            fetchmany_nbrows_b = len(rows)
            eq_(fetchmany_rowcount_b, NB_FETCH_TOTAL)
            eq_(fetchmany_nbrows_b, NB_FETCH_TOTAL - fetchmany_nbrows_a - 2)

            eq_(NB_FETCH_TOTAL, fetchmany_nbrows_a + fetchmany_nbrows_b + 2)

            cursor.execute(DROP_TABLE_DUMMY)

            spans = writer.pop()
            assert_greater_equal(len(spans), 6)
        finally:
            conn.close()
