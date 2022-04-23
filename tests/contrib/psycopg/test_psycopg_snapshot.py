import psycopg2
import pytest

from ddtrace.contrib.psycopg.patch import patch
from ddtrace.contrib.psycopg.patch import unpatch
from ddtrace.vendor import wrapt
from tests.contrib.config import POSTGRES_CONFIG
from tests.utils import override_config


@pytest.fixture(autouse=True)
def patch_psycopg():
    patch()
    assert isinstance(psycopg2.connect, wrapt.ObjectProxy)
    yield
    unpatch()


@pytest.mark.snapshot()
def test_connect_default():
    """By default we do not trace psycopg2.connect method"""
    conn = psycopg2.connect(**POSTGRES_CONFIG)
    assert conn


@pytest.mark.snapshot()
def test_connect_traced():
    """When explicitly enabled, we trace psycopg2.connect method"""
    with override_config("psycopg", {"trace_connect": True}):
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        assert conn


@pytest.mark.subprocess(env={"DD_PSYCOPG_TRACE_CONNECT": "true"})
@pytest.mark.snapshot(token="tests.contrib.psycopg.test_psycopg_snapshot.test_connect_traced")
def test_connect_traced_via_env():
    """When explicitly enabled, we trace psycopg2.connect method"""
    import psycopg2

    import ddtrace
    from tests.contrib.config import POSTGRES_CONFIG

    ddtrace.patch_all()

    conn = psycopg2.connect(**POSTGRES_CONFIG)
    assert conn
