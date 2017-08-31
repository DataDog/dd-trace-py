# stdlib
import logging
import unittest
from threading import Event

# 3p
from nose.tools import eq_
from nose.plugins.attrib import attr
from cassandra.cluster import Cluster, ResultSet
from cassandra.query import BatchStatement, SimpleStatement

# project
from tests.contrib.config import CASSANDRA_CONFIG
from tests.test_tracer import get_dummy_tracer
from ddtrace.contrib.cassandra.patch import patch, unpatch
from ddtrace.contrib.cassandra.session import get_traced_cassandra, SERVICE
from ddtrace.ext import net, cassandra as cassx, errors
from ddtrace import Pin


logging.getLogger('cassandra').setLevel(logging.INFO)


def setUpModule():
    # skip all the modules if the Cluster is not available
    if not Cluster:
        raise unittest.SkipTest("cassandra.cluster.Cluster is not available.")

    # create the KEYSPACE for this test module
    cluster = Cluster(port=CASSANDRA_CONFIG['port'])
    cluster.connect().execute("CREATE KEYSPACE if not exists test WITH REPLICATION = { 'class' : 'SimpleStrategy', 'replication_factor': 1}")
    cluster.connect().execute("CREATE TABLE if not exists test.person (name text PRIMARY KEY, age int, description text)")

def tearDownModule():
    # destroy the KEYSPACE
    cluster = Cluster(port=CASSANDRA_CONFIG['port'])
    cluster.connect().execute("DROP KEYSPACE IF EXISTS test")


class CassandraBase(object):
    """
    Needs a running Cassandra
    """
    TEST_QUERY = "SELECT * from test.person WHERE name = 'Cassandra'"
    TEST_QUERY_PAGINATED = "SELECT * from test.person"
    TEST_KEYSPACE = "test"
    TEST_PORT = str(CASSANDRA_CONFIG['port'])
    TEST_SERVICE = 'test-cassandra'

    def _traced_session(self):
        # implement me
        pass

    def tearDown(self):
        self.cluster.connect().execute('TRUNCATE test.person')

    def setUp(self):
        self.cluster = Cluster(port=CASSANDRA_CONFIG['port'])
        self.session = self.cluster.connect()
        self.session.execute("INSERT INTO test.person (name, age, description) VALUES ('Cassandra', 100, 'A cruel mistress')")
        self.session.execute("INSERT INTO test.person (name, age, description) VALUES ('Athena', 100, 'Whose shield is thunder')")
        self.session.execute("INSERT INTO test.person (name, age, description) VALUES ('Calypso', 100, 'Softly-braided nymph')")

    def _assert_result_correct(self, result):
        eq_(len(result.current_rows), 1)
        for r in result:
            eq_(r.name, "Cassandra")
            eq_(r.age, 100)
            eq_(r.description, "A cruel mistress")

    def _test_query_base(self, execute_fn):
        session, writer = self._traced_session()
        result = execute_fn(session, self.TEST_QUERY)
        self._assert_result_correct(result)

        spans = writer.pop()
        assert spans, spans

        # another for the actual query
        eq_(len(spans), 1)

        query = spans[0]
        eq_(query.service, self.TEST_SERVICE)
        eq_(query.resource, self.TEST_QUERY)
        eq_(query.span_type, cassx.TYPE)

        eq_(query.get_tag(cassx.KEYSPACE), self.TEST_KEYSPACE)
        eq_(query.get_tag(net.TARGET_PORT), self.TEST_PORT)
        eq_(query.get_tag(cassx.ROW_COUNT), "1")
        eq_(query.get_tag(cassx.PAGE_NUMBER), None)
        eq_(query.get_tag(cassx.PAGINATED), 'False')
        eq_(query.get_tag(net.TARGET_HOST), "127.0.0.1")


    def test_query(self):
        def execute_fn(session, query):
            return session.execute(query)
        self._test_query_base(execute_fn)

    def test_query_async(self):
        def execute_fn(session, query):
            event = Event()
            result = []
            future = session.execute_async(query)
            def callback(results):
                result.append(ResultSet(future, results))
                event.set()
            future.add_callback(callback)
            event.wait()
            return result[0]
        self._test_query_base(execute_fn)

    def test_query_async_clearing_callbacks(self):
        def execute_fn(session, query):
            future = session.execute_async(query)
            future.clear_callbacks()
            return future.result()
        self._test_query_base(execute_fn)

    def test_paginated_query(self):
        session, writer = self._traced_session()
        statement = SimpleStatement(self.TEST_QUERY_PAGINATED, fetch_size=1)
        result = session.execute(statement)
        #iterate over all pages
        results = list(result)
        eq_(len(results), 3)

        spans = writer.pop()
        assert spans, spans

        # There are 4 spans for 3 results since the driver makes a request with
        # no result to check that it has reached the last page
        eq_(len(spans), 4)

        for i in range(4):
            query = spans[i]
            eq_(query.service, self.TEST_SERVICE)
            eq_(query.resource, self.TEST_QUERY_PAGINATED)
            eq_(query.span_type, cassx.TYPE)

            eq_(query.get_tag(cassx.KEYSPACE), self.TEST_KEYSPACE)
            eq_(query.get_tag(net.TARGET_PORT), self.TEST_PORT)
            if i == 3:
                eq_(query.get_tag(cassx.ROW_COUNT), "0")
            else:
                eq_(query.get_tag(cassx.ROW_COUNT), "1")
            eq_(query.get_tag(net.TARGET_HOST), "127.0.0.1")
            eq_(query.get_tag(cassx.PAGINATED), 'True')
            eq_(query.get_tag(cassx.PAGE_NUMBER), str(i+1))

    def test_trace_with_service(self):
        session, writer = self._traced_session()
        session.execute(self.TEST_QUERY)
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        query = spans[0]
        eq_(query.service, self.TEST_SERVICE)

    def test_trace_error(self):
        session, writer = self._traced_session()
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
        for k in (errors.ERROR_MSG, errors.ERROR_TYPE):
            assert query.get_tag(k)

    @attr('bound')
    def test_bound_statement(self):
        session, writer = self._traced_session()

        query = "INSERT INTO test.person (name, age, description) VALUES (?, ?, ?)"
        prepared = session.prepare(query)
        session.execute(prepared, ("matt", 34, "can"))

        prepared = session.prepare(query)
        bound_stmt = prepared.bind(("leo", 16, "fr"))
        session.execute(bound_stmt)

        spans = writer.pop()
        eq_(len(spans), 2)
        for s in spans:
            eq_(s.resource, query)

    def test_batch_statement(self):
        session, writer = self._traced_session()

        batch = BatchStatement()
        batch.add(SimpleStatement("INSERT INTO test.person (name, age, description) VALUES (%s, %s, %s)"), ("Joe", 1, "a"))
        batch.add(SimpleStatement("INSERT INTO test.person (name, age, description) VALUES (%s, %s, %s)"), ("Jane", 2, "b"))
        session.execute(batch)

        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.resource, 'BatchStatement')
        eq_(s.get_metric('cassandra.batch_size'), 2)
        assert 'test.person' in s.get_tag('cassandra.query')


class TestCassPatchDefault(CassandraBase):
    """Test Cassandra instrumentation with patching and default configuration"""

    TEST_SERVICE = SERVICE

    def tearDown(self):
        unpatch()
        CassandraBase.tearDown(self)

    def setUp(self):
        CassandraBase.setUp(self)
        patch()

    def _traced_session(self):
        tracer = get_dummy_tracer()
        Pin.get_from(self.cluster).clone(tracer=tracer).onto(self.cluster)
        return self.cluster.connect(self.TEST_KEYSPACE), tracer.writer

class TestCassPatchAll(TestCassPatchDefault):
    """Test Cassandra instrumentation with patching and custom service on all clusters"""

    TEST_SERVICE = 'test-cassandra-patch-all'

    def tearDown(self):
        unpatch()
        CassandraBase.tearDown(self)

    def setUp(self):
        CassandraBase.setUp(self)
        patch()

    def _traced_session(self):
        tracer = get_dummy_tracer()
        # pin the global Cluster to test if they will conflict
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(Cluster)
        self.cluster = Cluster(port=CASSANDRA_CONFIG['port'])

        return self.cluster.connect(self.TEST_KEYSPACE), tracer.writer


class TestCassPatchOne(TestCassPatchDefault):
    """Test Cassandra instrumentation with patching and custom service on one cluster"""

    TEST_SERVICE = 'test-cassandra-patch-one'

    def tearDown(self):
        unpatch()
        CassandraBase.tearDown(self)

    def setUp(self):
        CassandraBase.setUp(self)
        patch()

    def _traced_session(self):
        tracer = get_dummy_tracer()
        # pin the global Cluster to test if they will conflict
        Pin(service='not-%s' % self.TEST_SERVICE).onto(Cluster)
        self.cluster = Cluster(port=CASSANDRA_CONFIG['port'])

        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(self.cluster)
        return self.cluster.connect(self.TEST_KEYSPACE), tracer.writer

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        tracer = get_dummy_tracer()
        Pin.get_from(Cluster).clone(tracer=tracer).onto(Cluster)

        session = Cluster(port=CASSANDRA_CONFIG['port']).connect(self.TEST_KEYSPACE)
        session.execute(self.TEST_QUERY)

        spans = tracer.writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        session = Cluster(port=CASSANDRA_CONFIG['port']).connect(self.TEST_KEYSPACE)
        session.execute(self.TEST_QUERY)

        spans = tracer.writer.pop()
        assert not spans, spans

        # Test patch again
        patch()
        Pin.get_from(Cluster).clone(tracer=tracer).onto(Cluster)

        session = Cluster(port=CASSANDRA_CONFIG['port']).connect(self.TEST_KEYSPACE)
        session.execute(self.TEST_QUERY)

        spans = tracer.writer.pop()
        assert spans, spans


def test_backwards_compat_get_traced_cassandra():
    cluster = get_traced_cassandra()
    session = cluster(port=CASSANDRA_CONFIG['port']).connect()
    session.execute("drop table if exists test.person")
