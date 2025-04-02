import os

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")


def test_code_injection_eval(iast_context_defaults):
    code_string = '"abc" + "def"'

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )
    mod.pt_eval(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == '"abc" + "def"'
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": '"abc" + "def"'}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_eval_globals(iast_context_defaults):
    """Validate globals and locals of the function"""

    code_string = "x + y"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )
    mod.pt_eval_globals(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == "x + y"
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "x + y"}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_eval_globals_locals(iast_context_defaults):
    """Validate globals and locals of the function"""

    code_string = "x + y"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )

    mod.pt_eval_globals_locals(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == "x + y"
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "x + y"}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_eval_globals_locals_override(iast_context_defaults):
    """Validate globals and locals of the function"""

    code_string = "x + y + z"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )
    with pytest.raises(NameError):
        mod.pt_eval_globals_locals(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == "x + y + z"
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "x + y + z"}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_eval_lambda(iast_context_defaults):
    """Validate globals and locals of the function"""
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")

    def pt_eval_lambda_no_tainted(fun):
        return eval("lambda v,fun=fun:not fun(v)")

    def is_true_no_tainted(value):
        return value is True

    assert mod.pt_eval_lambda(mod.is_true)(True) is pt_eval_lambda_no_tainted(is_true_no_tainted)(True)


def test_code_injection_eval_globals_kwargs_lambda(iast_context_defaults):
    """Validate globals and locals of the function"""

    code_string = "square(5)"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )

    mod.pt_eval_lambda_globals(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == "square(5)"
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "square(5)"}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_literal_eval(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")
    code_string = "[1, 2, 3]"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )
    mod.pt_literal_eval(tainted_string)

    data = _get_span_report()

    assert data is None
