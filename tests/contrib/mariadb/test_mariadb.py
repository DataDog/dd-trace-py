from typing import Tuple

import mariadb
import pytest

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.mariadb import patch
from ddtrace.contrib.mariadb import unpatch
from tests.contrib.config import MARIADB_CONFIG
from tests.utils import DummyTracer
from tests.utils import assert_dict_issuperset
from tests.utils import assert_is_measured
from tests.utils import override_config
from tests.utils import snapshot


MARIADB_VERSION = mariadb.__version_info__  # type: Tuple[int, int, int, str, int]
SNAPSHOT_VARIANTS = {
    "pre_1_1": MARIADB_VERSION < (1, 1, 0),
    "post_1_1": MARIADB_VERSION
    >= (
        1,
        1,
    ),
}


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    patch()
    try:
        yield tracer
    finally:
        unpatch()


def get_connection(tracer):
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
        span.get_tags(),
        {
            "out.host": u"127.0.0.1",
            "db.name": u"test",
            "db.user": u"test",
        },
    )


def test_query_executemany(connection, tracer):
    tracer.enabled = False
    cursor = connection.cursor()

    cursor.execute(
        """
        create table if not exists dummy (
            dummy_key VARCHAR(32) PRIMARY KEY,
            dummy_value TEXT NOT NULL)"""
    )
    cursor.execute("DELETE FROM dummy")
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


@pytest.mark.subprocess(env=dict(DD_SERVICE="mysvc"))
@snapshot(async_mode=False, variants=SNAPSHOT_VARIANTS)
def test_user_specified_dd_service_snapshot():
    """
    When a user specifies a service for the app
        The mariadb integration should not use it.
    """
    import mariadb

    from ddtrace import config  # noqa
    from ddtrace import patch
    from ddtrace import tracer

    patch(mariadb=True)
    from tests.contrib.config import MARIADB_CONFIG

    connection = mariadb.connect(**MARIADB_CONFIG)
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    tracer.shutdown()


@pytest.mark.subprocess(env=dict(DD_MARIADB_SERVICE="mysvc"))
@snapshot(async_mode=False, variants=SNAPSHOT_VARIANTS)
def test_user_specified_dd_mariadb_service_snapshot():
    """
    When a user specifies a service for the app
        The mariadb integration should not use it.
    """
    import mariadb

    from ddtrace import config  # noqa
    from ddtrace import patch
    from ddtrace import tracer

    patch(mariadb=True)
    from tests.contrib.config import MARIADB_CONFIG

    connection = mariadb.connect(**MARIADB_CONFIG)
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1
    tracer.shutdown()


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_simple_query_snapshot(tracer):
    with get_connection(tracer) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS, ignores=["meta.error.stack"])
def test_simple_malformed_query_snapshot(tracer):
    with get_connection(tracer) as connection:
        cursor = connection.cursor()
        with pytest.raises(mariadb.ProgrammingError):
            cursor.execute("SELEC 1")


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_simple_query_fetchall_snapshot(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        with get_connection(tracer) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_query_with_several_rows_snapshot(tracer):
    with get_connection(tracer) as connection:
        cursor = connection.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_query_with_several_rows_fetchall_snapshot(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        with get_connection(tracer) as connection:
            cursor = connection.cursor()
            query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 3


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_query_many_fetchall_snapshot(tracer):
    with override_config("mariadb", dict(trace_fetch_methods=True)):
        with get_connection(tracer) as connection:

            # tests that the executemany method is correctly wrapped.
            tracer.enabled = False
            cursor = connection.cursor()

            cursor.execute(
                """
                create table if not exists dummy (
                    dummy_key VARCHAR(32) PRIMARY KEY,
                    dummy_value TEXT NOT NULL)"""
            )
            cursor.execute("DELETE FROM dummy")
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


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_commit_snapshot(tracer):
    with get_connection(tracer) as connection:
        connection.commit()


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_query_proc_snapshot(tracer):
    with get_connection(tracer) as connection:
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


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_analytics_with_rate_snapshot(tracer):
    with override_config("mariadb", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        with get_connection(tracer) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1


@snapshot(include_tracer=True, variants=SNAPSHOT_VARIANTS)
def test_analytics_without_rate_snapshot(tracer):
    with override_config("mariadb", dict(analytics_enabled=True)):
        with get_connection(tracer) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
