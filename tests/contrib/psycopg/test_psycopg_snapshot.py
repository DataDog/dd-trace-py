import os
import sys

import psycopg
import pytest

from ddtrace.contrib.psycopg.patch import patch
from ddtrace.contrib.psycopg.patch import unpatch
from ddtrace.vendor import wrapt
from tests.contrib.config import POSTGRES_CONFIG
from tests.utils import override_config


@pytest.fixture(autouse=True)
def patch_psycopg():
    patch()
    assert isinstance(psycopg.connect, wrapt.ObjectProxy)
    assert isinstance(psycopg.Cursor, wrapt.ObjectProxy)
    assert isinstance(psycopg.AsyncCursor, wrapt.ObjectProxy)

    # check if Connection connect methods are patched
    try:
        assert isinstance(psycopg.Connection.connect, wrapt.ObjectProxy)
        assert isinstance(psycopg.AsyncConnection.connect, wrapt.ObjectProxy)
    except AttributeError:
        if sys.version_info >= (3, 11):
            # Python 3.11 is throwing an AttributeError when accessing a BoundMethod
            # __get__ for Psycopg.Connection.connect method.
            pass
        else:
            pytest.fail(
                "Connection and AsyncConnection objects should have their connect methods \
                patched and verified."
            )
    yield
    unpatch()


@pytest.mark.snapshot()
def test_connect_default():
    """By default we do not trace psycopg.connect method"""
    conn = psycopg.connect(**POSTGRES_CONFIG)
    assert conn


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_connect_traced():
    """When explicitly enabled, we trace psycopg.connect method"""
    with override_config("psycopg", {"trace_connect": True}):
        conn = psycopg.connect(**POSTGRES_CONFIG)
        assert conn


@pytest.mark.snapshot(token="tests.contrib.psycopg.test_psycopg_snapshot.test_connect_traced", wait_for_num_traces=1)
def test_connect_traced_via_env(run_python_code_in_subprocess):
    """When explicitly enabled, we trace psycopg.connect method"""

    code = """
import psycopg

import ddtrace
from tests.contrib.config import POSTGRES_CONFIG

ddtrace.patch_all()

conn = psycopg.connect(**POSTGRES_CONFIG)
assert conn
    """

    env = os.environ.copy()
    env["DD_PSYCOPG_TRACE_CONNECT"] = "true"
    out, err, status, pid = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert out == b"", err
