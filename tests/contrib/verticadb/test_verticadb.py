# stdlib

# 3p
import vertica_python

# project
from ddtrace.contrib.verticadb.patch import patch, unpatch

# testing
import pytest
from tests.contrib.config import VERTICA_CONFIG


class TestVerticaDB(object):
    def setup_method(self, method):
        self.TEST_TABLE = 'test_table'

    def teardown_method(self, method):
        pass

    def test_connection(self):
        conn = vertica_python.connect(**VERTICA_CONFIG)
        with conn:
            cur = conn.cursor()
            cur.execute('DROP TABLE IF EXISTS {}'.format(self.TEST_TABLE))
            cur.execute('CREATE TABLE {} (a INT, b VARCHAR(32))'.format(self.TEST_TABLE))
