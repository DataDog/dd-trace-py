import os
from typing import Tuple  # noqa:F401

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
        tracer.pop_traces()
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


def test_connection_no_port_or_user_does_not_raise():
    conf = MARIADB_CONFIG.copy()
    del conf["port"]
    del conf["user"]
    try:
        mariadb.connect(**conf)
    except mariadb.OperationalError as exc:
        # this error is expected because mariadb defaults user to root when not given
        if "Access denied for user 'root'" not in str(exc):
            raise exc


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
    assert span.get_metric("network.destination.port") == 3306

    assert_dict_issuperset(
        span.get_tags(),
        {
            "out.host": "127.0.0.1",
            "db.name": "test",
            "db.system": "mariadb",
            "db.user": "test",
            "component": "mariadb",
            "server.address": "127.0.0.1",
            "span.kind": "client",
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


@pytest.mark.parametrize(
    "service_schema",
    [
        (None, None),
        (None, "v0"),
        (None, "v1"),
        ("mysvc", None),
        ("mysvc", "v0"),
        ("mysvc", "v1"),
    ],
)
@pytest.mark.snapshot(variants=SNAPSHOT_VARIANTS)
def test_schematized_service_and_operation(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    code = """
import pytest
import sys
import mariadb

from ddtrace import patch

def test():
    patch(mariadb=True)
    from tests.contrib.config import MARIADB_CONFIG

    connection = mariadb.connect(**MARIADB_CONFIG)
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    if service:
        env["DD_SERVICE"] = service

    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


@pytest.mark.subprocess(env=dict(DD_MARIADB_SERVICE="mysvc"))
@pytest.mark.snapshot(variants=SNAPSHOT_VARIANTS)
def test_user_specified_dd_mariadb_service_snapshot():
    """
    When a user specifies a service for the app
        The mariadb integration should not use it.
    """
    import mariadb

    from ddtrace import patch

    patch(mariadb=True)
    from tests.contrib.config import MARIADB_CONFIG

    connection = mariadb.connect(**MARIADB_CONFIG)
    cursor = connection.cursor()
    cursor.execute("SELECT 1")
    rows = cursor.fetchall()
    assert len(rows) == 1


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
