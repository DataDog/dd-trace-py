import pytest
import mariadb 
from tests.utils import DummyTracer
from tests.utils import assert_is_measured
from tests.utils import assert_dict_issuperset



@pytest.fixture
def tracer():
    tracer = DummyTracer()
    # Yield to our test
    yield tracer
    tracer.pop()


@pytest.fixture
def connection(tracer):
    connection = mariadb.connect(
            user="user",
            password="user",
            host="127.0.0.1",
            port=3306,
            database="employees"
        )
    Pin.override(connection, tracer=tracer)
#need to figure out where the code to make the db goes, previously it went in an init
#db file but now maybe i should just use the connection to create a database here?
#     CREATE DATABASE IF NOT EXISTS `test`;
# GRANT ALL ON `test`.* TO 'user'@'%';
    yield connection
    connection.close()


def test_simple_query(connection, tracer):
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert_is_measured(span)
    assert span.service == "mariadb"
    assert span.name == "mariadb.query"
    assert span.span_type == "sql"
    assert span.error == 0
    assert span.get_metric("out.port") == 3306
    assert_dict_issuperset(
            span.meta,
            {
                "out.host": u"127.0.0.1",
                "db.name": u"test",
                "db.user": u"test",
            },
        )

def test_simple_query_fetchll(connection, tracer):
    ##Overide the tracer rather than the self object (how it's done in test_mysql.py)
    tracer.override_config("dbapi2", dict(trace_fetch_methods=True))
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()
    assert len(spans) == 2

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "mariadb"
    assert span.name == "mariadb.query"
    assert span.span_type == "sql"
    assert span.error == 0
    assert span.get_metric("out.port") == 3306
    assert_dict_issuperset(
        span.meta,
        {
            "out.host": u"127.0.0.1",
            "db.name": u"test",
            "db.user": u"test",
        },
    )

    assert spans[1].name == "mariadb.query.fetchall"
    
def test_simple_query_fetchll(connection, tracer):
    with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
        cursor = connection.cursor
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 2

        span = spans[0]
        assert_is_measured(span)
        assert span.service == "mariadb"
        assert span.name == "mariadb.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("out.port") == 3306
        assert_dict_issuperset(
            span.meta,
            {
                "out.host": u"127.0.0.1",
                "db.name": u"test",
                "db.user": u"test",
            },
        )

        assert spans[1].name == "mariadb.query.fetchall"

def test_query_with_several_rows(connection, tracer):
    cursor = connection.cursor()
    query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
    cursor.execute(query)
    rows = cursor.fetchall()
    assert len(rows) == 3
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.get_tag("sql.query") is None

def test_query_with_several_rows_fetchall(connection, tracer):
    with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
        cursor = connection.cursor
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3
        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[0]
        assert span.get_tag("sql.query") is None
        assert spans[1].name == "mariadb.query.fetchall"

def test_query_many(connection, tracer):
    # tests that the executemany method is correctly wrapped.
    tracer.enabled = False
    cursor = connection.cursor

    cursor.execute(
        """
        create table if not exists dummy (
            dummy_key VARCHAR(32) PRIMARY KEY,
            dummy_value TEXT NOT NULL)"""
    )
    tracer.enabled = True

    stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
    data = [
        ("foo", "this is foo"),
        ("bar", "this is bar"),
    ]
    cursor.executemany(stmt, data)
    query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
    cursor.execute(query)
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "bar"
    assert rows[0][1] == "this is bar"
    assert rows[1][0] == "foo"
    assert rows[1][1] == "this is foo"

    spans = tracer.pop()
    assert len(spans) == 2
    span = spans[-1]
    assert span.get_tag("sql.query") is None
    cursor.execute("drop table if exists dummy")

def test_query_many_fetchall(connection, tracer):
    with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
        # tests that the executemany method is correctly wrapped.
        tracer.enabled = False
        cursor = connection.cursor

        cursor.execute(
            """
            create table if not exists dummy (
                dummy_key VARCHAR(32) PRIMARY KEY,
                dummy_value TEXT NOT NULL)"""
        )
        tracer.enabled = True

        stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
        data = [
            ("foo", "this is foo"),
            ("bar", "this is bar"),
        ]
        cursor.executemany(stmt, data)
        query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "bar"
        assert rows[0][1] == "this is bar"
        assert rows[1][0] == "foo"
        assert rows[1][1] == "this is foo"

        spans = tracer.pop()
        assert len(spans) == 3
        span = spans[-1]
        assert span.get_tag("sql.query") is None
        cursor.execute("drop table if exists dummy")

        assert spans[2].name == "mariadb.query.fetchall"

def test_query_proc(connection, tracer):

    # create a procedure
    tracer.enabled = False
    cursor = connection.cursor
    cursor.execute("DROP PROCEDURE IF EXISTS sp_sum")
    cursor.execute(
        """
        CREATE PROCEDURE sp_sum (IN p1 INTEGER, IN p2 INTEGER, OUT p3 INTEGER)
        BEGIN
            SET p3 := p1 + p2;
        END;"""
    )

    tracer.enabled = True
    proc = "sp_sum"
    data = (40, 2, None)
    output = cursor.callproc(proc, data)
    assert len(output) == 3
    assert output[2] == 42

    spans = tracer.pop()
    assert spans, spans

    # number of spans depends on mariadb implementation details,
    # typically, internal calls to execute, but at least we
    # can expect the last closed span to be our proc.
    span = spans[len(spans) - 1]
    assert_is_measured(span)
    assert span.service == "mariadb"
    assert span.name == "mariadb.query"
    assert span.span_type == "sql"
    assert span.error == 0
    assert span.get_metric("out.port") == 3306
    assert_dict_issuperset(
        span.meta,
        {
            "out.host": u"127.0.0.1",
            "db.name": u"test",
            "db.user": u"test",
        },
    )
    assert span.get_tag("sql.query") is None

def test_simple_query_ot(connection, tracer):
    """OpenTracing version of test_simple_query."""

    ot_tracer = init_tracer("mariadb_svc", tracer)

    with ot_tracer.start_active_span("mariadb_op"):
        cursor = connection.cursor
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1

    spans = tracer.pop()
    assert len(spans) == 2

    ot_span, dd_span = spans

    # confirm parenting
    assert ot_span.parent_id is None
    assert dd_span.parent_id == ot_span.span_id

    assert ot_span.service == "mariadb_svc"
    assert ot_span.name == "mariadb_op"

    assert_is_measured(dd_span)
    assert dd_span.service == "mariadb"
    assert dd_span.name == "mariadb.query"
    assert dd_span.span_type == "sql"
    assert dd_span.error == 0
    assert dd_span.get_metric("out.port") == 3306
    assert_dict_issuperset(
        dd_span.meta,
        {
            "out.host": u"127.0.0.1",
            "db.name": u"test",
            "db.user": u"test",
        },
    )

def test_simple_query_ot_fetchall(connection, tracer):
    """OpenTracing version of test_simple_query."""
    with self.override_config("dbapi2", dict(trace_fetch_methods=True)):

        ot_tracer = init_tracer("mariadb_svc", tracer)

        with ot_tracer.start_active_span("mariadb_op"):
            cursor = connection.cursor
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1

        spans = tracer.pop()
        assert len(spans) == 3

        ot_span, dd_span, fetch_span = spans

        # confirm parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.service == "mariadb_svc"
        assert ot_span.name == "mariadb_op"

        assert_is_measured(dd_span)
        assert dd_span.service == "mariadb"
        assert dd_span.name == "mariadb.query"
        assert dd_span.span_type == "sql"
        assert dd_span.error == 0
        assert dd_span.get_metric("out.port") == 3306
        assert_dict_issuperset(
            dd_span.meta,
            {
                "out.host": u"127.0.0.1",
                "db.name": u"test",
                "db.user": u"test",
            },
        )

        assert fetch_span.name == "mariadb.query.fetchall"

def test_commit(connection, tracer):
    conn.commit()
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "mariadb"
    assert span.name == "mariadb.connection.commit"

def test_rollback(connection, tracer):
    conn.rollback()
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "mariadb"
    assert span.name == "mariadb.connection.rollback"

def test_analytics_default(connection, tracer):
    cursor = connection.cursor
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()

    self.assertEqual(len(spans), 1)
    span = spans[0]
    self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

def test_analytics_with_rate(connection, tracer):
    with self.override_config("dbapi2", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        cursor = connection.cursor
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

def test_analytics_without_rate(connection, tracer):
    with self.override_config("dbapi2", dict(analytics_enabled=True)):
        cursor = connection.cursor
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

@TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
def test_user_specified_service(connection, tracer):
    """
    When a user specifies a service for the app
        The mariadb integration should not use it.
    """
    # Ensure that the service name was configured
    from ddtrace import config

    assert config.service == "mysvc"

    cursor = connection.cursor
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()

    assert spans[0].service != "mysvc"

##how to convert this? Pass in tracer object and just test that?
class TestmariadbPatch(mariadbCore, TracerTestCase):
def setUp(connection, tracer):
    super(TestmariadbPatch, connection, tracer).setUp()
    patch()

def tearDown(connection, tracer):
    super(TestmariadbPatch, connection, tracer).tearDown()
    unpatch()

def _get_conn_tracer(connection, tracer):
    if not self.conn:
        self.conn = mariadb.connector.connect(**mariadb_CONFIG)
        assert self.conn.is_connected()
        # Ensure that the default pin is there, with its default value
        pin = Pin.get_from(self.conn)
        assert pin
        # assert pin.service == 'mariadb'
        # Customize the service
        # we have to apply it on the existing one since new one won't inherit `app`
        pin.clone(tracer=self.tracer).onto(self.conn)

        return self.conn, self.tracer

def test_patch_unpatch(connection, tracer):
    unpatch()
    # assert we start unpatched
    connection = mariadb.connector.connect(**mariadb_CONFIG)
    assert not Pin.get_from(conn)
    connection.close()

    patch()
    try:
        connection = mariadb.connector.connect(**mariadb_CONFIG)
        pin = Pin.get_from(conn)
        assert pin
        pin.clone(service="pin-svc", tracer=self.tracer).onto(conn)
        assert connection.is_connected()

        cursor = connection.cursor
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.service == "pin-svc"
        assert span.name == "mariadb.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("out.port") == 3306
        assert_dict_issuperset(
            span.meta,
            {
                "out.host": u"127.0.0.1",
                "db.name": u"test",
                "db.user": u"test",
            },
        )
        assert span.get_tag("sql.query") is None

    finally:
        unpatch()

        # assert we finish unpatched
        connection = mariadb.connector.connect(**mariadb_CONFIG)
        assert not Pin.get_from(connection)
        connection.close()

    patch()

@TracerTestCase.run_in_subprocess(env_overrides=dict(DD_mariadb_SERVICE="mysvc"))
def test_user_specified_service_integration(connection, tracer):
    cursor = connection.cursor
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()

    assert spans[0].service == "mysvc"