#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from ddtrace.contrib.mysql import missing_modules

from nose.tools import eq_

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

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

class MySQLTest(unittest.TestCase):
    SERVICE = 'test-db'

    def setUp(self):
        True

    def tearDown(self):
        True

    def test_connection(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        conn.close()

    def test_simple_query(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer,
                                            service=MySQLTest.SERVICE,
                                            meta={META_KEY: META_VALUE})
        conn = MySQL(**MYSQL_CONFIG)
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
            'sql.db': u'mysql',
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
            'sql.db': u'mysql',
            META_KEY: META_VALUE,
             })
        eq_(span.get_metric('sql.rows'), 1)

        conn.close()

    def test_query_with_several_rows(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT n FROM "
                       "(SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m")
        rows = cursor.fetchall()
        eq_(len(rows), 3)
        conn.close()

    def test_query_many(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
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
        conn.close()

    def test_cursor_buffered_raw(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
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

    def test_connection_buffered_raw(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        for buffered in (None, False, True):
            for raw in (None, False, True):
                conn = MySQL(buffered=buffered, raw=raw, **MYSQL_CONFIG)
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
