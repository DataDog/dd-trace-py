import json

import pytest
import responses
import snowflake.connector


if snowflake.connector.VERSION >= (2, 3, 0):
    req_mock = responses.RequestsMock(target="snowflake.connector.vendored.requests.adapters.HTTPAdapter.send")
else:
    req_mock = responses.RequestsMock(target="snowflake.connector.network.HTTPAdapter.send")


def snowflake_response(
    mock, url, method=responses.POST, data=None, success=True, status=200, content_type="application/json"
):
    body = {
        "code": status,
        "success": success,
    }
    if data is not None:
        body["data"] = data

    return mock.add(method, url, body=json.dumps(body), status=status, content_type=content_type)


@pytest.fixture
def client():
    with req_mock:
        snowflake_response(
            req_mock,
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


@req_mock.activate
def test_snowflake(client):
    pass
