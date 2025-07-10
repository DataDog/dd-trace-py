from ddtrace.appsec._constants import TELEMETRY_INFORMATION_NAME
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.contrib.internal.sqlalchemy.patch import patch as sqli_sqlalchemy_patch
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch as sqlalchemy_unpatch
from ddtrace.contrib.internal.sqlite3.patch import patch as sqli_sqlite3_patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch as sqli_sqlite_unpatch
from tests.appsec.iast.test_telemetry import _assert_instrumented_sink
from tests.utils import override_global_config


def test_metric_instrumented_sqli_sqlite3(telemetry_writer):
    sqli_sqlite_unpatch()
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        sqli_sqlite3_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)


def test_metric_instrumented_sqli_sqlalchemy_patch(telemetry_writer):
    sqlalchemy_unpatch()
    with override_global_config(dict(_iast_enabled=True, _iast_telemetry_report_lvl=TELEMETRY_INFORMATION_NAME)):
        sqli_sqlalchemy_patch()

    _assert_instrumented_sink(telemetry_writer, VULN_SQL_INJECTION)
