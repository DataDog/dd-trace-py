from logging import ERROR
import pytest
import mariadb
from tests.utils import DummyTracer
from tests.utils import assert_is_measured
from tests.utils import assert_dict_issuperset
from tests.utils import override_config
from tests.utils import TracerTestCase
from ddtrace.contrib.mariadb import patch
from ddtrace.contrib.mariadb import unpatch
from ddtrace import Pin
from ddtrace import tracer as global_tracer
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from tests.utils import snapshot
from tests.contrib.config import MARIADB_CONFIG
import unittest



@pytest.fixture
def tracer():
    tracer = DummyTracer()
    patch()
    # Yield to our test
    try:
        yield tracer
    finally:
        unpatch()


def get_connection(tracer):
    # Some test cases need a connection to be created post-configuration
    connection = mariadb.connect(**MARIADB_CONFIG)
    Pin.override(connection, tracer=tracer)

    return connection


@pytest.fixture
def connection(tracer):
    with get_connection(tracer) as connection:
        yield connection


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


def test_simple_query_fetchall(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        connection = get_connection(tracer)
        from ddtrace import config

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


def test_query_with_several_rows(connection, tracer):
    cursor = connection.cursor()
    query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
    cursor.execute(query)
    rows = cursor.fetchall()
    assert len(rows) == 3
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.get_tag("mariadb.query") is None


def test_query_with_several_rows_fetchall(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        connection = get_connection(tracer)
        cursor = connection.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3
        spans = tracer.pop()
        assert len(spans) == 2
        span = spans[0]
        assert span.get_tag("mariadb.query") is None
        assert spans[1].name == "mariadb.query.fetchall"


def test_query_many(connection, tracer):
    # tests that the executemany method is correctly wrapped.
    tracer.enabled = False
    cursor = connection.cursor()

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
    assert span.get_tag("mariadb.query") is None
    cursor.execute("drop table if exists dummy")


def test_query_many_fetchall(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        connection = get_connection(tracer)

        # tests that the executemany method is correctly wrapped.
        tracer.enabled = False
        cursor = connection.cursor()

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
        assert span.get_tag("mariadb.query") is None
        cursor.execute("drop table if exists dummy")

        assert spans[2].name == "mariadb.query.fetchall"


def test_query_proc(connection, tracer):

    # create a procedure
    tracer.enabled = False
    cursor = connection.cursor()
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
    cursor.callproc(proc, data)

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
    assert span.get_tag("mariadb.query") is None


def test_commit(connection, tracer):
    connection.commit()
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "mariadb"
    assert span.name == "mariadb.connection.commit"


def test_rollback(connection, tracer):
    connection.rollback()
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.service == "mariadb"
    assert span.name == "mariadb.connection.rollback"


def test_analytics_default(connection, tracer):
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    spans = tracer.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None


def test_analytics_with_rate(connection, tracer):
    with override_config("mariadb", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5


def test_analytics_without_rate(connection, tracer):
    with override_config("mariadb", dict(analytics_enabled=True)):
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()

        assert len(spans) == 1

        span = spans[0]
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0


@pytest.mark.parametrize(
    "service_env_key,service_env_value", [("DD_SERVICE", "mysvc"), ("DD_MARIADB_SERVICE", "mysvc")]
)
def test_user_specified_service_snapshot(run_python_code_in_subprocess, service_env_key, service_env_value):
    """
    When a user specifies a service for the app
        The mariadb integration should not use it.
    """

    @snapshot(
        async_mode=False,
        token_override="tests.contrib.mariadb.test_mariadb.test_user_specified_service_snapshot_{}_{}".format(
            service_env_key, service_env_value
        ),
    )
    def testcase():
        out, err, status, pid = run_python_code_in_subprocess(
            """
from ddtrace import config
from ddtrace import patch
import mariadb
patch(mariadb=True)
from tests.contrib.config import MARIADB_CONFIG
connection = mariadb.connect(**MARIADB_CONFIG)
cursor = connection.cursor()
cursor.execute("SELECT 1")
rows = cursor.fetchall()
assert len(rows) == 1
""",
            env={service_env_key: service_env_value},
        )
        assert status == 0, err

    testcase()


@snapshot(include_tracer=True)
def test_simple_query_snapshot(tracer):
    connection = get_connection(tracer)
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1


@snapshot(include_tracer=True, ignores=["meta.error.stack"])
def test_simple_malformed_query_snapshot(tracer):
    connection = get_connection(tracer)
    cursor = connection.cursor()
    with pytest.raises(mariadb.ProgrammingError):
        cursor.execute("SELEC 1")


@snapshot(include_tracer=True)
def test_simple_query_fetchall_snapshot(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        connection = get_connection(tracer)
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1


@snapshot(include_tracer=True)
def test_commit_snapshot(tracer):
    connection = get_connection(tracer)
    connection.commit()


@snapshot(include_tracer=True)
def test_query_proc_snapshot(tracer):
    connection = get_connection(tracer)
    # create a procedure
    tracer.enabled = False
    cursor = connection.cursor()
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
    cursor.callproc(proc, data)
