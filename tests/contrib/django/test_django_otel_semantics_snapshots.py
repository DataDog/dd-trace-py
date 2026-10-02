"""Django is the one server integration with OTel semantics snapshots; they cover every server case in the RFC."""

import django
import pytest

from tests.contrib.django.test_django_snapshots import daphne_client


# Middleware spans differ between Django versions and the request span carries everything under test.
OTEL_ENV = {"DD_DJANGO_INSTRUMENT_MIDDLEWARE": "false"}
# asgi.version, http.version and django.user.is_authenticated are only reported by newer Django/asgiref
# releases, the default user agent depends on the installed requests version, and the error stack
# embeds file paths and line numbers.
OTEL_IGNORES = [
    "user_agent.original",
    "django.response.class",
    "asgi.version",
    "http.version",
    "django.user.is_authenticated",
    "error.stack",
]

pytestmark = pytest.mark.skipif(django.VERSION < (2, 0), reason="")


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_request():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("fn-view/", timeout=10).status_code == 200


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_request_unaccepted_method():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        client.request("PROPFIND", "fn-view/", timeout=10)


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_unmatched_route():
    # No route matched, so the span is named from the method alone and is not an error.
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("nonexistent/", timeout=10).status_code == 404


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_redirect_is_not_an_error():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("fn-view", timeout=10, allow_redirects=False).status_code == 301


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_exception_is_an_error():
    # A view that raises is answered with a 500, which is an error whose error.type is the status code.
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("error-500/", timeout=10).status_code == 500


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_url_query_is_obfuscated():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("fn-view/?token=leaked&page=2", timeout=10).status_code == 200


@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_custom_error_statuses():
    # A configured error range takes precedence over the OTel default, and error.type is the status code.
    env = dict(OTEL_ENV, DD_TRACE_HTTP_SERVER_ERROR_STATUSES="200")
    with daphne_client("application", additional_env=env) as (client, _):
        assert client.get("fn-view/", timeout=10).status_code == 200


@pytest.mark.snapshot(otel_semantics=True, ignores=[i for i in OTEL_IGNORES if i != "user_agent.original"])
def test_otel_semantics_user_agent_and_client_addresses():
    # Client addresses are only collected when client IP collection is enabled.
    env = dict(OTEL_ENV, DD_TRACE_CLIENT_IP_ENABLED="true")
    with daphne_client("application", additional_env=env) as (client, _):
        assert client.get("fn-view/", timeout=10, headers={"User-Agent": "otel-semantics-test"}).status_code == 200
