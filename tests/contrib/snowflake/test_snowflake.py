import json

import pytest
import responses
import snowflake.connector

from ddtrace.contrib.snowflake import patch
from ddtrace.contrib.snowflake import unpatch
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


def add_snowflake_query_response(rowtype, rows):
    data = {
        "queryResponseFormat": "json",
        "rowtype": [SNOWFLAKE_TYPES[t] for t in rowtype],
        "rowset": rows,
        "total": len(rows),
    }

    add_snowflake_response(url="https://mock-account.snowflakecomputing.com:443/queries/v1/query-request", data=data)


@pytest.fixture
def client():
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


@snapshot()
@req_mock.activate
def test_snowflake(client):
    add_snowflake_query_response(
        rowtype=["TEXT"],
        rows=[("4.30.2",)],
    )
    with client.cursor() as cur:
        cur.execute("select current_version();")
        assert cur.fetchone() == ("4.30.2",)
