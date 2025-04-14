from pathlib import PosixPath
from unittest import mock

import pytest

from ddtrace import patch
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.appsec._iast.taint_sinks.sql_injection import check_and_report_sqli
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data


DDBBS = [
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_sqlite3.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_sqlite3",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_mysqldb.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_mysqldb",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_pymysql.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_pymysql",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_psycopg2.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_psycopg2",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_sqlalchemy.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_sqlalchemy",
    ),
]


def setup_module():
    patch(pymysql=True, mysqldb=True)


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection(fixture_path, fixture_module, iast_context_defaults):
    mod = _iast_patched_module(fixture_module)
    table = taint_pyobject(
        pyobject="students",
        source_name="test_ossystem",
        source_value="students",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(table)

    mod.sqli_simple(table)
    data = _get_iast_data()
    assert len(data["vulnerabilities"]) >= 1
    # We will pick up weak hash vulnerabilities in some db connector libraries
    # but we are only interested in SQL Injection vulnerabilities
    sqli_vulnerabilities = [x for x in data["vulnerabilities"] if x["type"] == VULN_SQL_INJECTION]
    assert len(sqli_vulnerabilities) == 1
    vulnerability = sqli_vulnerabilities[0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_SQL_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "SELECT "},
        {"redacted": True},
        {"value": " FROM "},
        {"value": "students", "source": 0},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert source["name"] == "test_ossystem"
    assert source["origin"] == OriginType.PARAMETER
    assert source["value"] == "students"

    line, hash_value = get_line_and_hash("test_sql_injection", VULN_SQL_INJECTION, filename=fixture_path)
    assert vulnerability["location"]["path"] == fixture_path
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == "sqli_simple"
    assert vulnerability["location"]["class_name"] == ""
    assert vulnerability["hash"] == hash_value


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection_deduplication(fixture_path, fixture_module, iast_context_deduplication_enabled):
    mod = _iast_patched_module(fixture_module)

    table = taint_pyobject(
        pyobject="students",
        source_name="test_ossystem",
        source_value="students",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(table)
    for _ in range(0, 5):
        mod.sqli_simple(table)

    data = _get_iast_data()
    # We will pick up weak hash vulnerabilities in some db connector libraries
    # but we are only interested in SQL Injection vulnerabilities
    sqli_vulnerabilities = [x for x in data["vulnerabilities"] if x["type"] == VULN_SQL_INJECTION]
    assert len(sqli_vulnerabilities) == 1
    VulnerabilityBase._prepare_report._reset_cache()


def test_checked_tainted_args(iast_context_defaults):
    cursor = mock.Mock()
    cursor.execute.__name__ = "execute"
    cursor.executemany.__name__ = "executemany"

    arg = "nobody expects the spanish inquisition"

    tainted_arg = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)

    untainted_arg = "gallahad the pure"

    # Returns False: Untainted first argument
    assert not check_and_report_sqli(
        args=(untainted_arg,), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns False: Untainted first argument
    assert not check_and_report_sqli(
        args=(untainted_arg, tainted_arg), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns False: Integration name not in list
    assert not check_and_report_sqli(
        args=(tainted_arg,),
        kwargs=None,
        integration_name="nosqlite",
        method=cursor.execute,
    )

    # Returns False: Wrong function name
    assert not check_and_report_sqli(
        args=(tainted_arg,),
        kwargs=None,
        integration_name="sqlite",
        method=cursor.executemany,
    )

    # Returns True:
    assert check_and_report_sqli(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="sqlite", method=cursor.execute
    )

    # Returns True:
    assert check_and_report_sqli(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="mysql", method=cursor.execute
    )

    # Returns False: No more QUOTA
    assert not check_and_report_sqli(
        args=(tainted_arg, untainted_arg), kwargs=None, integration_name="psycopg", method=cursor.execute
    )


@pytest.mark.parametrize(
    "args,integration_name,expected_result",
    (
        (
            [
                "nobody expects the spanish inquisition",
            ],
            "sqlite",
            True,
        ),
        (
            [
                "gallahad the pure",
            ],
            "sqlite",
            True,
        ),
        (
            [
                b"gallahad the pure",
            ],
            "sqlite",
            True,
        ),
        (
            [
                bytearray(b"gallahad the pure"),
            ],
            "sqlite",
            True,
        ),
        (
            [
                "gallahad the pure" * 100,
            ],
            "sqlite",
            True,
        ),
    ),
)
def test_check_and_report_sqli_metrics(args, integration_name, expected_result, iast_context_defaults):
    cursor = mock.Mock()
    cursor.execute.__name__ = "execute"

    args[0] = taint_pyobject(
        args[0], source_name="request_body", source_value=args[0], source_origin=OriginType.PARAMETER
    )

    with mock.patch(
        "ddtrace.appsec._iast.taint_sinks.sql_injection.increment_iast_span_metric"
    ) as mock_increment, mock.patch(
        "ddtrace.appsec._iast.taint_sinks.sql_injection._set_metric_iast_executed_sink"
    ) as mock_set_metric:
        # Call with tainted argument that should trigger metrics
        result = check_and_report_sqli(args=args, kwargs={}, integration_name=integration_name, method=cursor.execute)

        assert result is expected_result
        mock_increment.assert_called_once()
        mock_set_metric.assert_called_once_with(VULN_SQL_INJECTION)


@pytest.mark.parametrize(
    "args,integration_name",
    (
        ((PosixPath("imnotastring"),), "sqlite"),
        (("",), "sqlite"),
        ((bytearray(b""),), "sqlite"),
        ((b"",), "sqlite"),
        (
            [
                "nobody expects the spanish inquisition",
            ],
            "sqlite1000",
        ),
        (
            [
                "gallahad the pure",
            ],
            "database1000",
        ),
    ),
)
def test_check_and_report_sqli_no_metrics(args, integration_name, iast_context_defaults):
    cursor = mock.Mock()
    cursor.execute.__name__ = "execute"

    with mock.patch(
        "ddtrace.appsec._iast.taint_sinks.sql_injection.increment_iast_span_metric"
    ) as mock_increment, mock.patch(
        "ddtrace.appsec._iast.taint_sinks.sql_injection._set_metric_iast_executed_sink"
    ) as mock_set_metric:
        # Call with untainted argument that should not trigger metrics
        result = check_and_report_sqli(args=args, kwargs={}, integration_name=integration_name, method=cursor.execute)

        assert result is False
        mock_increment.assert_not_called()
        mock_set_metric.assert_not_called()
