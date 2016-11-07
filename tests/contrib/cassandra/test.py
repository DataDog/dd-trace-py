
# stdlib
import unittest

# 3p
from nose.tools import eq_
from cassandra.cluster import Cluster

# project
from tests.contrib.config import CASSANDRA_CONFIG
from tests.test_tracer import get_test_tracer
from ddtrace.contrib.cassandra.session import get_traced_cassandra, patch_cluster
from ddtrace.ext import net, cassandra as cassx, errors
from ddtrace import Pin


class CassandraBase(object): #unittest.TestCase):
    """
    Needs a running Cassandra
    """
    TEST_QUERY = "SELECT * from test.person"
    TEST_KEYSPACE = "test"
    TEST_PORT = str(CASSANDRA_CONFIG['port'])

    def setUp(self):
        if not Cluster:
            raise unittest.SkipTest("cassandra.cluster.Cluster is not available.")

        self.cluster = Cluster(port=CASSANDRA_CONFIG['port'])
        session = self.cluster.connect()
        session.execute("""CREATE KEYSPACE if not exists test WITH REPLICATION = {
            'class' : 'SimpleStrategy',
            'replication_factor': 1
        }""")
        session.execute("CREATE TABLE if not exists test.person (name text PRIMARY KEY, age int, description text)")
        session.execute("""INSERT INTO test.person (name, age, description) VALUES ('Cassandra', 100, 'A cruel mistress')""")

    def _assert_result_correct(self, result):
        eq_(len(result.current_rows), 1)
        for r in result:
            eq_(r.name, "Cassandra")
            eq_(r.age, 100)
            eq_(r.description, "A cruel mistress")

    def test_get_traced_cassandra(self):
        session, writer = self._traced_session("cassandra")
        result = session.execute(self.TEST_QUERY)
        self._assert_result_correct(result)

        spans = writer.pop()
        assert spans, spans

        # another for the actual query
        eq_(len(spans), 1)

        query = spans[0]
        eq_(query.service, "cassandra")
        eq_(query.resource, self.TEST_QUERY)
        eq_(query.span_type, cassx.TYPE)

        eq_(query.get_tag(cassx.KEYSPACE), self.TEST_KEYSPACE)
        eq_(query.get_tag(net.TARGET_PORT), self.TEST_PORT)
        eq_(query.get_tag(cassx.ROW_COUNT), "1")
        eq_(query.get_tag(net.TARGET_HOST), "127.0.0.1")

    def test_trace_with_service(self):
        session, writer = self._traced_session("custom")
        session.execute(self.TEST_QUERY)
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        query = spans[0]
        eq_(query.service, "custom")

    def test_trace_error(self):
        session, writer = self._traced_session("foo")
        try:
            session.execute("select * from test.i_dont_exist limit 1")
        except Exception:
            pass
        else:
            assert 0

        spans = writer.pop()
        assert spans
        query = spans[0]
        eq_(query.error, 1)
        for k in (errors.ERROR_MSG, errors.ERROR_TYPE, errors.ERROR_STACK):
            assert query.get_tag(k)

    def tearDown(self):
        self.cluster.connect().execute("DROP KEYSPACE IF EXISTS test")


class TestOldSchool(CassandraBase):

    def _traced_session(self, service):
        tracer = get_test_tracer()
        TracedCluster = get_traced_cassandra(tracer, service=service)
        session = TracedCluster(port=CASSANDRA_CONFIG['port']).connect(self.TEST_KEYSPACE)
        return session, tracer.writer


class TestCassPatch(CassandraBase):

    def _traced_session(self, service):
        tracer = get_test_tracer()
        cluster = Cluster(port=CASSANDRA_CONFIG['port'])

        pin = Pin(service=service, tracer=tracer)
        patch_cluster(cluster, pin=pin)
        return cluster.connect(self.TEST_KEYSPACE), tracer.writer
