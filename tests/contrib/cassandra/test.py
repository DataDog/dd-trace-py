import unittest
from nose.tools import eq_

from ddtrace.contrib.cassandra import missing_modules
if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

from cassandra.cluster import Cluster
from ddtrace.contrib.cassandra import trace as get_traced_cassandra
from ddtrace.tracer import Tracer

from ...test_tracer import DummyWriter

class CassandraTest(unittest.TestCase):
    """Needs a running cassandra at localhost:9042"""

    TEST_QUERY = "SELECT * from test.person"
    TEST_KEYSPACE = "test"

    def setUp(self):
        if not Cluster:
            raise unittest.SkipTest("cassandra.cluster.Cluster is not available.")

        self.cluster = Cluster(port=9042)
        session = self.cluster.connect()
        session.execute("""CREATE KEYSPACE test WITH REPLICATION = {
            'class' : 'SimpleStrategy',
            'replication_factor': 1
        }""")
        session.execute("CREATE TABLE test.person (name text PRIMARY KEY, age int, description text)")
        session.execute("""INSERT INTO test.person (name, age, description) VALUES ('Cassandra', 100, 'A cruel mistress')""")


    def _assert_result_correct(self, result):
        eq_(len(result.current_rows), 1)
        for r in result:
            eq_(r.name, "Cassandra")
            eq_(r.age, 100)
            eq_(r.description, "A cruel mistress")

    def test_cassandra_instance(self):
        """
        Tests patching a cassandra Session instance
        """
        writer = DummyWriter()
        tracer = Tracer(writer=writer)


        TracedCluster = get_traced_cluster(tracer)
        session = TracedCluster(port=9042).connect()

        result = session.execute(self.TEST_QUERY)
        self._assert_result_correct(result)

        spans = writer.pop()
        assert spans

        # Should be sending one request to "USE <keyspace>" and another for the actual query
        eq_(len(spans), 2)
        use, query = spans[0], spans[1]

        eq_(use.service, "cassandra")
        eq_(use.resource, "USE %s" % self.TEST_KEYSPACE)
        eq_(query.service, "cassandra")
        eq_(query.resource, self.TEST_QUERY)
        eq_(query.get_tag(cassx.KEYSPACE), self.TEST_KEYSPACE)
        eq_(query.get_tag(netx.TARGET_PORT), "9042")
        eq_(query.get_tag(cassx.ROW_COUNT), "1")
        eq_(query.get_tag(netx.TARGET_HOST), "127.0.0.1")

    def tearDown(self):
        self.cluster.connect().execute("DROP KEYSPACE IF EXISTS test")
