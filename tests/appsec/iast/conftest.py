import logging
import os
import subprocess
import time

import pytest

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context import end_iast_context
from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context import start_iast_context
from ddtrace.appsec._iast._patches.json_tainting import patch as json_patch
from ddtrace.appsec._iast._patches.json_tainting import unpatch_iast as json_unpatch
from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
from ddtrace.appsec._iast.taint_sinks.code_injection import unpatch as code_injection_unpatch
from ddtrace.appsec._iast.taint_sinks.command_injection import patch as cmdi_patch
from ddtrace.appsec._iast.taint_sinks.command_injection import unpatch as cmdi_unpatch
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import unpatch as header_injection_unpatch
from ddtrace.appsec._iast.taint_sinks.weak_cipher import patch as weak_cipher_patch
from ddtrace.appsec._iast.taint_sinks.weak_cipher import unpatch_iast as weak_cipher_unpatch
from ddtrace.appsec._iast.taint_sinks.weak_hash import patch as weak_hash_patch
from ddtrace.appsec._iast.taint_sinks.weak_hash import unpatch_iast as weak_hash_unpatch
from ddtrace.contrib.internal.sqlite3.patch import patch as sqli_sqlite_patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch as sqli_sqlite_unpatch
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from tests.appsec.iast.iast_utils import IAST_VALID_LOG
from tests.utils import override_env
from tests.utils import override_global_config


CONFIG_SERVER_PORT = "9596"


@pytest.fixture
def no_request_sampling(tracer):
    with override_env(
        {
            IAST.ENV_REQUEST_SAMPLING: "100",
            "DD_IAST_MAX_CONCURRENT_REQUEST": "100",
        }
    ):
        oce.reconfigure()
        yield


def _start_iast_context_and_oce(span=None):
    oce.reconfigure()
    request_iast_enabled = False
    if oce.acquire_request(span):
        start_iast_context()
        request_iast_enabled = True
    set_iast_request_enabled(request_iast_enabled)


def _end_iast_context_and_oce(span=None):
    end_iast_context(span)
    oce.release_request()


def iast_context(env, request_sampling=100.0, deduplication=False, asm_enabled=False):
    try:
        from ddtrace.contrib.internal.langchain.patch import patch as langchain_patch
        from ddtrace.contrib.internal.langchain.patch import unpatch as langchain_unpatch
    except Exception:
        langchain_patch = lambda: True  # noqa: E731
        langchain_unpatch = lambda: True  # noqa: E731
    try:
        from ddtrace.contrib.internal.sqlalchemy.patch import patch as sqlalchemy_patch
        from ddtrace.contrib.internal.sqlalchemy.patch import unpatch as sqlalchemy_unpatch
    except Exception:
        sqlalchemy_patch = lambda: True  # noqa: E731
        sqlalchemy_unpatch = lambda: True  # noqa: E731
    try:
        from ddtrace.contrib.internal.psycopg.patch import patch as psycopg_patch
        from ddtrace.contrib.internal.psycopg.patch import unpatch as psycopg_unpatch
    except Exception:
        psycopg_patch = lambda: True  # noqa: E731
        psycopg_unpatch = lambda: True  # noqa: E731

    class MockSpan:
        _trace_id_64bits = 17577308072598193742

    env.update({"DD_IAST_DEDUPLICATION_ENABLED": str(deduplication)})
    with override_global_config(
        dict(
            _asm_enabled=asm_enabled,
            _iast_enabled=True,
            _iast_deduplication_enabled=deduplication,
            _iast_request_sampling=request_sampling,
        )
    ), override_env(env):
        _start_iast_context_and_oce(MockSpan())
        weak_hash_patch()
        weak_cipher_patch()
        sqli_sqlite_patch()
        json_patch()
        psycopg_patch()
        sqlalchemy_patch()
        cmdi_patch()
        header_injection_patch()
        code_injection_patch()
        langchain_patch()
        patch_common_modules()
        yield
        unpatch_common_modules()
        weak_hash_unpatch()
        weak_cipher_unpatch()
        sqli_sqlite_unpatch()
        json_unpatch()
        psycopg_unpatch()
        sqlalchemy_unpatch()
        cmdi_unpatch()
        header_injection_unpatch()
        code_injection_unpatch()
        langchain_unpatch()
        _end_iast_context_and_oce()


@pytest.fixture
def iast_context_defaults():
    yield from iast_context(dict(DD_IAST_ENABLED="true"))


@pytest.fixture
def iast_context_deduplication_enabled(tracer):
    yield from iast_context(dict(DD_IAST_ENABLED="true"), deduplication=True)


@pytest.fixture
def iast_span_defaults(tracer):
    for _ in iast_context(dict(DD_IAST_ENABLED="true")):
        with tracer.trace("test") as span:
            yield span


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "skip_iast_check_logs: mark test to remove _DD_IAST_DEBUG environment variable and skip logs checks to validate"
        "if the propagation is not running outside the context",
    )


@pytest.fixture(autouse=True)
def check_native_code_exception_in_each_python_aspect_test(request, caplog):
    if "skip_iast_check_logs" in request.keywords:
        yield
    else:
        with override_env({"_DD_IAST_USE_ROOT_SPAN": "false"}), override_global_config(
            dict(_iast_debug=True)
        ), caplog.at_level(logging.DEBUG):
            yield

        log_messages = [record.message for record in caplog.get_records("call")]

        for message in log_messages:
            if IAST_VALID_LOG.search(message):
                pytest.fail(message)
        # TODO(avara1986): iast tests throw a timeout in gitlab
        #   list_metrics_logs = list(telemetry_writer._logs)
        #   assert len(list_metrics_logs) == 0


@pytest.fixture(scope="session")
def configuration_endpoint():
    current_dir = os.path.dirname(__file__)
    status = None
    retries = 0
    while status != 200 and retries < 5:
        cmd = [
            "python",
            os.path.join(current_dir, "fixtures", "integration", "http_config_server.py"),
            CONFIG_SERVER_PORT,
        ]
        try:
            process = subprocess.Popen(cmd, cwd=current_dir)
            time.sleep(0.9)

            url = f"http://localhost:{CONFIG_SERVER_PORT}/"
            conn = get_connection(url)

            conn.request("GET", "/")
            response = conn.getresponse()
            result = Response.from_http_response(response)
            status = result.status
        except (OSError, ConnectionError, PermissionError):
            pass
        retries += 1

    if retries == 5:
        pytest.skip("Failed to start the configuration server")

    yield
    process.kill()


@pytest.fixture(autouse=True)
def clear_iast_env_vars():
    os.environ[IAST.PATCH_MODULES] = "benchmarks.,tests.appsec."
    if IAST.DENY_MODULES in os.environ:
        os.environ.pop("_DD_IAST_DENY_MODULES")
    yield
