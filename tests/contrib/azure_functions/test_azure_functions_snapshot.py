import json

import pytest

from tests.webclient import Client


DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}

DISTRIBUTED_TRACING_DISABLED_PARAMS = {
    "DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": "False",
}


@pytest.mark.snapshot
def test_http_get_ok(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetok?key=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_async(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetokasync?key=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_obfuscated(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetok?secret=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_async_obfuscated(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetokasync?secret=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_http_get_error(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgeterror", headers=DEFAULT_HEADERS).status_code == 500


@pytest.mark.snapshot
def test_http_post_ok(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post("/api/httppostok", headers=DEFAULT_HEADERS, data={"key": "val"}).status_code == 200
    )


@pytest.mark.snapshot
def test_http_get_trigger_arg(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgettriggerarg", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_decorator(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetfunctionnamedecorator", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_no_decorator(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetfunctionnamenodecorator", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_decorator_order(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.get("/api/httpgetfunctionnamedecoratororder", headers=DEFAULT_HEADERS).status_code == 200
    )


@pytest.mark.parametrize(
    "azure_functions_client",
    [{}, DISTRIBUTED_TRACING_DISABLED_PARAMS],
    ids=["enabled", "disabled"],
    indirect=True,
)
@pytest.mark.snapshot
def test_http_get_distributed_tracing(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetroot", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_timer(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/timer",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": None}),
        ).status_code
        == 202
    )


@pytest.mark.snapshot
def test_timer_async(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/timer_async",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": None}),
        ).status_code
        == 202
    )
