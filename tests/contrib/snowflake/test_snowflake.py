import contextlib
import json
import os

import pytest
import responses
import snowflake.connector

from ddtrace import Pin
from ddtrace import tracer
from ddtrace.contrib.snowflake import patch
from ddtrace.contrib.snowflake import unpatch
from tests.opentracer.utils import init_tracer
from tests.utils import override_config
from tests.utils import snapshot


if snowflake.connector.VERSION >= (2, 3, 0):
    req_mock = responses.RequestsMock(target="snowflake.connector.vendored.requests.adapters.HTTPAdapter.send")
else:
    req_mock = responses.RequestsMock(target="snowflake.connector.network.HTTPAdapter.send")


SNOWFLAKE_TYPES = {
    "TEXT": {
        "type": "TEXT",
        "name": "current_version",
        "length": None,
        "precision": None,
        "scale": None,
        "nullable": False,
    }
}


def add_snowflake_response(
    url, method=responses.POST, data=None, success=True, status=200, content_type="application/json"
):
    body = {
        "code": status,
        "success": success,
    }
    if data is not None:
        body["data"] = data

    return req_mock.add(method, url, body=json.dumps(body), status=status, content_type=content_type)


def add_snowflake_query_response(rowtype, rows, total=None):
    if total is None:
        total = len(rows)
    data = {
        "queryResponseFormat": "json",
        "rowtype": [SNOWFLAKE_TYPES[t] for t in rowtype],
        "rowset": rows,
        "total": total,
        "queryId": "mocksfqid-29244a72-90a0-4309-9168-9dcf67721f6d",
    }

    add_snowflake_response(url="https://mock-account.snowflakecomputing.com:443/queries/v1/query-request", data=data)


@contextlib.contextmanager
def _client():
    patch()
    with req_mock:
        add_snowflake_response(
            "https://mock-account.snowflakecomputing.com:443/session/v1/login-request",
            data={
                "token": "mock-token",
                "masterToken": "mock-master-token",
                "id_token": "mock-id-token",
                "sessionId": "mock-session-id",
                "sessionInfo": {
                    "databaseName": "mock-db-name",
                    "schemaName": "mock-schema-name",
                    "warehouseName": "mock-warehouse-name",
                    "roleName": "mock-role-name",
                },
            },
        )
        ctx = snowflake.connector.connect(user="mock-user", password="mock-password", account="mock-account")
    try:
        yield ctx
    finally:
        ctx.close()
        unpatch()


@pytest.fixture
def client():
    with _client() as ctx:
        yield ctx


@contextlib.contextmanager
def ot_trace():
    ot = init_tracer("snowflake_svc", tracer)
    with ot.start_active_span("snowflake_op"):
        yield


@snapshot()
@req_mock.activate
def test_snowflake_fetchone(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with client.cursor() as cur:
        res = cur.execute("select current_version();")
        assert res == cur
        assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_settings_override(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with override_config("snowflake", dict(service="my-snowflake-svc")):
        with client.cursor() as cur:
            res = cur.execute("select current_version();")
            assert res == cur
            assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_analytics_with_rate(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with override_config("snowflake", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        with client.cursor() as cur:
            res = cur.execute("select current_version();")
            assert res == cur
            assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_analytics_without_rate(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with override_config("snowflake", dict(analytics_enabled=True)):
        with client.cursor() as cur:
            res = cur.execute("select current_version();")
            assert res == cur
            assert cur.fetchone() == ("4.30.2",)


@pytest.mark.subprocess(env=dict(DD_SNOWFLAKE_SERVICE="env-svc"), err=None)
@snapshot()
def test_snowflake_service_env():
    from tests.contrib.snowflake.test_snowflake import _client
    from tests.contrib.snowflake.test_snowflake import add_snowflake_query_response
    from tests.contrib.snowflake.test_snowflake import req_mock

    with _client() as c:
        with req_mock:
            add_snowflake_query_response(
                rowtype=["TEXT"],
                rows=[("4.30.2",)],
            )

            with c.cursor() as cur:
                res = cur.execute("select current_version();")
                assert res == cur
                assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_pin_override(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    Pin(service="pin-sv", tags={"custom": "tag"}).onto(client)
    with client.cursor() as cur:
        res = cur.execute("select current_version();")
        assert res == cur
        assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_commit(client):
    add_snowflake_query_response(rowtype=[], rows=[])
    client.commit()


@snapshot()
@req_mock.activate
def test_snowflake_rollback(client):
    add_snowflake_query_response(rowtype=[], rows=[])
    client.rollback()


@snapshot()
@req_mock.activate
def test_snowflake_fetchall(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with client.cursor() as cur:
        res = cur.execute("select current_version();")
        assert res == cur
        assert cur.fetchall() == [("4.30.2",)]


@snapshot()
@req_mock.activate
def test_snowflake_fetchall_multiple_rows(client):
    add_snowflake_query_response(
        rowtype=["TEXT", "TEXT"],
        rows=[("1a", "1b"), ("2a", "2b")],
    )
    with client.cursor() as cur:
        res = cur.execute("select a, b from t;")
        assert res == cur
        assert cur.fetchall() == [
            ("1a", "1b"),
            ("2a", "2b"),
        ]


@snapshot()
@req_mock.activate
def test_snowflake_executemany_insert(client):
    add_snowflake_query_response(
        rowtype=[],
        rows=[],
        total=2,
    )
    with client.cursor() as cur:
        res = cur.executemany(
            "insert into t (a, b) values (%s, %s);",
            [
                ("1a", "1b"),
                ("2a", "2b"),
            ],
        )
        assert res == cur
        assert res.rowcount == 2


@snapshot()
@req_mock.activate
def test_snowflake_ot_fetchone(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with ot_trace():
        with client.cursor() as cur:
            res = cur.execute("select current_version();")
            assert res == cur
            assert cur.fetchone() == ("4.30.2",)


@snapshot()
@req_mock.activate
def test_snowflake_ot_fetchall(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with ot_trace():
        with client.cursor() as cur:
            res = cur.execute("select current_version();")
            assert res == cur
            assert cur.fetchall() == [("4.30.2",)]


@snapshot()
@req_mock.activate
def test_snowflake_ot_fetchall_multiple_rows(client):
    add_snowflake_query_response(
        rowtype=["TEXT", "TEXT"],
        rows=[("1a", "1b"), ("2a", "2b")],
    )
    with ot_trace():
        with client.cursor() as cur:
            res = cur.execute("select a, b from t;")
            assert res == cur
            assert cur.fetchall() == [
                ("1a", "1b"),
                ("2a", "2b"),
            ]


@snapshot()
@req_mock.activate
def test_snowflake_ot_executemany_insert(client):
    add_snowflake_query_response(
        rowtype=[],
        rows=[],
        total=2,
    )
    with ot_trace():
        with client.cursor() as cur:
            res = cur.executemany(
                "insert into t (a, b) values (%s, %s);",
                [
                    ("1a", "1b"),
                    ("2a", "2b"),
                ],
            )
            assert res == cur
            assert res.rowcount == 2


@pytest.mark.snapshot()
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
def test_schematization(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    code = """
import pytest
import sys

from tests.contrib.snowflake.test_snowflake import _client
from tests.contrib.snowflake.test_snowflake import add_snowflake_query_response
from tests.contrib.snowflake.test_snowflake import req_mock
def test_snowflake_service_env():

    with _client() as c:
        with req_mock:
            add_snowflake_query_response(
                rowtype=["TEXT"],
                rows=[("4.30.2",)],
            )

            with c.cursor() as cur:
                res = cur.execute("select current_version();")
                assert res == cur
                assert cur.fetchone() == ("4.30.2",)

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    env["DD_TRACE_REQUESTS_ENABLED"] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()
