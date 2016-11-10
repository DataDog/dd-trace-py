#!/usr/bin/env python
# -*- coding: utf-8 -*-

# stdlib
import unittest

# 3p
import mysql
from mysql.connector import __version__ as connector_version
from nose.tools import eq_, assert_greater_equal

# project
from ddtrace import Tracer, Pin
from ddtrace.contrib.mysql import get_traced_mysql_connection
from ddtrace.contrib.mysql.tracers import patch_conn
from tests.test_tracer import DummyWriter, get_dummy_tracer
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


SERVICE = 'test-db'
CLASSNAME_MATRIX = ({"buffered": None,
                     "raw": None,
                     "baseclass_name": "MySQLCursor"},
                    {"buffered": None,
                     "raw": False,
                     "baseclass_name": "MySQLCursor"},
                    {"buffered": None,
                     "raw": True,
                     "baseclass_name": "MySQLCursorRaw"},
                    {"buffered": False,
                     "raw": None,
                     "baseclass_name": "MySQLCursor"},
                    {"buffered": False,
                     "raw": False,
                     "baseclass_name": "MySQLCursor"},
                    {"buffered": False,
                     "raw": True,
                     "baseclass_name": "MySQLCursorRaw"},
                    {"buffered": True,
                     "raw": None,
                     "baseclass_name": "MySQLCursorBuffered"},
                    {"buffered": True,
                     "raw": False,
                     "baseclass_name": "MySQLCursorBuffered"},
                    {"buffered": True,
                     "raw": True,
                     "baseclass_name": "MySQLCursorBufferedRaw"},
)

conn = None

def tearDown():
    # FIXME: get rid of jumbo try/finally and
    # let this tearDown close all connections
    if conn and conn.is_connected():
        conn.close()

def test_connection():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(**MYSQL_CONFIG)
    assert conn
    assert conn.is_connected()

def test_simple_query():
    tracer = get_dummy_tracer()
    writer = tracer.writer
    conn = patch_conn(mysql.connector.connect(**MYSQL_CONFIG))
    pin = Pin.get_from(conn)
    assert pin
    pin.tracer = tracer
    pin.service = SERVICE
    pin.onto(conn)

    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    eq_(len(rows), 1)
    spans = writer.pop()
    eq_(len(spans), 1)

    span = spans[0]
    eq_(span.service, SERVICE)
    eq_(span.name, 'mysql.query')
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

def test_simple_fetch():
    # Tests a simple query with a fetch, enabling fetch tracing."""
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer,
                                        service=SERVICE,
                                        meta={META_KEY: META_VALUE},
                                        trace_fetch=True)
    conn = MySQL(**MYSQL_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    eq_(len(rows), 1)
    spans = writer.pop()
    eq_(len(spans), 2)

    span = spans[0]
    eq_(span.service, SERVICE)
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
    eq_(span.service, SERVICE)
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

def test_query_with_several_rows():
    # Tests that multiple rows are returned.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(**MYSQL_CONFIG)
    cursor = conn.cursor()
    query = "SELECT n FROM " \
            "(SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
    cursor.execute(query)
    rows = cursor.fetchall()
    eq_(len(rows), 3)

    spans = writer.pop()
    assert_greater_equal(len(spans), 1)
    for span in spans:
        eq_(span.get_tag('sql.query'), query)

def test_query_many():
    # tests that the executemany method is correctly wrapped.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(**MYSQL_CONFIG)
    cursor = conn.cursor()
    cursor.execute(CREATE_TABLE_DUMMY)

    stmt = "INSERT INTO dummy (dummy_key,dummy_value) VALUES (%s, %s)"
    data = [("foo","this is foo"),
            ("bar","this is bar")]
    cursor.executemany(stmt, data)
    query = "SELECT dummy_key, dummy_value FROM dummy " \
            "ORDER BY dummy_key"
    cursor.execute(query)
    rows = cursor.fetchall()
    eq_(len(rows), 2)
    eq_(rows[0][0], "bar")
    eq_(rows[0][1], "this is bar")
    eq_(rows[1][0], "foo")
    eq_(rows[1][1], "this is foo")

    spans = writer.pop()
    assert_greater_equal(len(spans), 2)
    span = spans[-1]
    eq_(span.get_tag('sql.query'), query)
    cursor.execute(DROP_TABLE_DUMMY)

def test_query_proc():
    # Tests that callproc works as expected, and generates a correct span.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(**MYSQL_CONFIG)
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
    eq_(span.service, SERVICE)
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

def test_fetch_variants():
    # Tests that calling different variants of fetch works,
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer,
                                        service=SERVICE,
                                        trace_fetch=True)
    conn = MySQL(**MYSQL_CONFIG)
    cursor = conn.cursor()

    cursor.execute(CREATE_TABLE_DUMMY)

    NB_FETCH_TOTAL = 30
    NB_FETCH_MANY = 5
    stmt = "INSERT INTO dummy (dummy_key,dummy_value) VALUES (%s, %s)"
    data = [("%02d" % i, "this is %d" % i) for i in range(NB_FETCH_TOTAL)]
    cursor.executemany(stmt, data)
    query = "SELECT dummy_key, dummy_value FROM dummy " \
            "ORDER BY dummy_key"
    cursor.execute(query)

    rows = cursor.fetchmany(size=NB_FETCH_MANY)
    fetchmany_rowcount_a = cursor.rowcount
    fetchmany_nbrows_a = len(rows)
    eq_(fetchmany_rowcount_a, NB_FETCH_MANY)
    eq_(fetchmany_nbrows_a, NB_FETCH_MANY)

    rows = cursor.fetchone()
    fetchone_rowcount_a = cursor.rowcount
    eq_(fetchone_rowcount_a, NB_FETCH_MANY + 1)
    # careful: rows contains only one line with the values,
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

    spans = writer.pop()
    assert_greater_equal(len(spans), 1)
    span = spans[-1]
    eq_(span.get_tag('sql.query'), query)
    cursor.execute(DROP_TABLE_DUMMY)

def check_connection_class(buffered, raw, baseclass_name):
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(buffered=buffered, raw=raw, **MYSQL_CONFIG)
    cursor = conn.cursor()
    eq_(cursor._datadog_baseclass_name, baseclass_name)
    query = "SELECT 1"
    cursor.execute(query)
    rows = cursor.fetchall()
    eq_(len(rows), 1)
    eq_(int(rows[0][0]), 1)

    spans = writer.pop()
    assert_greater_equal(len(spans), 1)
    for span in spans:
        eq_(span.get_tag('sql.query'), query)

def test_connection_class():
    """
    Tests what class the connection constructor returns for different
    combination of raw and buffered parameter. This is important as
    any bug in our code at this level could result in silent bugs for
    our customers, we want to make double-sure the right class is
    instanciated.
    """
    for cases in CLASSNAME_MATRIX:
        f = check_connection_class
        setattr(f, "description", "Class returned by Connection.__init__() "
                "when raw=%(raw)s buffered=%(buffered)s" % cases)
        yield f, cases["buffered"], cases["raw"], cases["baseclass_name"]

def check_cursor_class(buffered, raw, baseclass_name):
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    MySQL = get_traced_mysql_connection(tracer, service=SERVICE)
    conn = MySQL(**MYSQL_CONFIG)
    cursor = conn.cursor(buffered=buffered, raw=raw)
    eq_(cursor._datadog_baseclass_name, baseclass_name)
    query = "SELECT 1"
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    eq_(len(rows), 1)
    eq_(int(rows[0][0]), 1)

    spans = writer.pop()
    assert_greater_equal(len(spans), 1)
    for span in spans:
        eq_(span.get_tag('sql.query'), query)

def test_cursor_class():
    # Tests what class the connection cursor() method returns for
    # different combination of raw and buffered parameter. This is
    # important as any bug in our code at this level could result in
    # silent bugs for our customers, we want to make double-sure the
    # right class is instanciated.
    for cases in CLASSNAME_MATRIX:
        f = check_cursor_class
        setattr(f, "description", "test_cursor_class_raw=%(raw)s_buffered=%(buffered)s" % cases)
        yield f, cases["buffered"], cases["raw"], cases["baseclass_name"]
