#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from ddtrace.contrib.mysql import missing_modules

from nose.tools import eq_

from ddtrace.tracer import Tracer
from ddtrace.contrib.mysql import get_traced_mysql_connection

from tests.test_tracer import DummyWriter
from tests.contrib.config import MYSQL_CONFIG

META_KEY = "i.am"
META_VALUE = "Your Father"

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

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE, meta={META_KEY: META_VALUE})
        conn = MySQL(**MYSQL_CONFIG)
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
            'sql.db': u'mysql',
            META_KEY: META_VALUE,
             })
        conn.close()

    def test_query_with_several_rows(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        MySQL = get_traced_mysql_connection(tracer, service=MySQLTest.SERVICE)
        conn = MySQL(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m")
        rows = cursor.fetchall()
        eq_(len(rows), 3)
        conn.close()
