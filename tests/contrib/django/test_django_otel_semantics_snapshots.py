import django
import pytest

from tests.contrib.django.test_django_snapshots import daphne_client


# Middleware spans differ between Django versions and the request span carries everything under test.
OTEL_ENV = {"DD_DJANGO_INSTRUMENT_MIDDLEWARE": "false"}
# asgi.version and http.version are only reported by newer Django/asgiref releases.
OTEL_IGNORES = ["user_agent.original", "django.response.class", "asgi.version", "http.version"]


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_request():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        assert client.get("fn-view/", timeout=10).status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@pytest.mark.snapshot(otel_semantics=True, ignores=OTEL_IGNORES)
def test_otel_semantics_request_unaccepted_method():
    with daphne_client("application", additional_env=OTEL_ENV) as (client, _):
        client.request("PROPFIND", "fn-view/", timeout=10)
