import os

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
    print(err)
    assert status == 0, err
    print(status)
    print(out)
    # assert (
    #     out
    #     == b"user=postgres dbname=postgres host=127.0.0.1\n{'user': \
    #     'postgres', 'dbname': 'postgres', 'host': '127.0.0.1'}\n"
    # ), err
    assert out == b"", err
