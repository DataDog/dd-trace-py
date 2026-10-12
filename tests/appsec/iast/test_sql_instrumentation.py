from unittest import mock

import pytest

from ddtrace.appsec._iast import load_iast
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.contrib.internal.sqlite3.patch import patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch
from ddtrace.internal import telemetry
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.utils import override_global_config


@pytest.mark.parametrize("iast_enabled", [False, True])
def test_sql_instrumentation_metric(iast_enabled):
    load_iast()
    unpatch()
    with (
        override_global_config(dict(_iast_enabled=iast_enabled)),
        mock.patch.object(telemetry.telemetry_writer, "add_count_metric") as metric,
    ):
        try:
            patch()
            patch()
            unpatch()
            patch()
            expected = mock.call(
                TELEMETRY_NAMESPACE.IAST,
                "instrumented.sink",
                1,
                (("vulnerability_type", VULN_SQL_INJECTION),),
            )
            assert metric.call_args_list == ([expected, expected] if iast_enabled else [])
        finally:
            unpatch()


def test_failed_sql_instrumentation_does_not_report_metric():
    load_iast()
    unpatch()
    with (
        override_global_config(dict(_iast_enabled=True)),
        mock.patch.object(telemetry.telemetry_writer, "add_count_metric") as metric,
        mock.patch("sqlite3._datadog_patch", False, create=True),
        mock.patch("ddtrace.contrib.internal.sqlite3.patch._w", side_effect=RuntimeError("patch failed")),
    ):
        with pytest.raises(RuntimeError, match="patch failed"):
            patch()
        metric.assert_not_called()


@pytest.mark.subprocess(env=dict(DD_IAST_ENABLED="true"), parametrize=dict(import_first=["true", "false"]), err=None)
def test_sql_instrumentation_bootstrap_order():
    import importlib
    import os
    from unittest import mock

    from ddtrace.internal import telemetry

    if os.environ["import_first"] == "true":
        importlib.import_module("sqlite3")

    with mock.patch.object(telemetry.telemetry_writer, "add_count_metric") as metric:
        import ddtrace.auto  # noqa: F401

        sqlite3 = importlib.import_module("sqlite3")
        sql_metrics = [
            call
            for call in metric.call_args_list
            if call.args[1] == "instrumented.sink" and call.args[3] == (("vulnerability_type", "SQL_INJECTION"),)
        ]
        assert len(sql_metrics) == 1
        with sqlite3.connect(":memory:") as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="false"),
    parametrize=dict(DD_APPSEC_ENABLED=["false", "true"]),
    err=None,
)
def test_sql_instrumentation_without_iast():
    from importlib.machinery import EXTENSION_SUFFIXES
    import os
    import sqlite3
    import sys

    import ddtrace.auto  # noqa: F401
    from ddtrace.contrib.internal.sqlite3.patch import patch
    from ddtrace.internal.settings.asm import config as asm_config

    patch()
    assert asm_config._asm_enabled == (os.environ["DD_APPSEC_ENABLED"] == "true")
    assert "ddtrace.appsec._iast._metrics" not in sys.modules
    assert "ddtrace.appsec._iast.constants" not in sys.modules
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    iast_extensions = [
        name
        for name, module in sys.modules.copy().items()
        if name.startswith("ddtrace.appsec._iast.")
        and (getattr(module, "__file__", None) or "").endswith(tuple(EXTENSION_SUFFIXES))
    ]
    assert not iast_extensions, iast_extensions
