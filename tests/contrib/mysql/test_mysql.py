#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from ddtrace.contrib.mysql import missing_modules

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

from nose.tools import eq_, ok_

from ddtrace.tracer import Tracer
from ddtrace.contrib.mysql import get_traced_mysql_connection

from tests.test_tracer import DummyWriter
from tests.contrib.config import MYSQL_CONFIG

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
        #cursor = conn.execute("SELECT 6*7 AS the_answer;")
