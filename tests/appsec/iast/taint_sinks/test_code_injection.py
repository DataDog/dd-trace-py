import os

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_code_injection_eval(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")
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


# TODO: wrap exec functions is very dangerous because it needs and modifies locals and globals from the original func
# def test_code_injection_exec(iast_context_defaults):
#     mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")
#     code_string = '"abc" + "def"'
#
#     tainted_string = taint_pyobject(
#         code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
#     )
#     mod.pt_exec(tainted_string)
#
#     data = _get_iast_data()
#
#     assert len(data["vulnerabilities"]) == 1
#     vulnerability = data["vulnerabilities"][0]
#     source = data["sources"][0]
#     assert vulnerability["type"] == VULN_CODE_INJECTION
#     assert source["name"] == "path"
#     assert source["origin"] == OriginType.PATH
#     assert source["value"] == '"abc" + "def"'
#     assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": '"abc" + "def"'}]
#     assert "value" not in vulnerability["evidence"].keys()
#     assert vulnerability["evidence"].get("pattern") is None
#     assert vulnerability["evidence"].get("redacted") is None
#
#
# def test_code_injection_exec_with_globals(iast_context_defaults):
#     mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")
#     code_string = 'my_var_in_pt_exec_with_globals + "-" + my_var_in_pt_exec_with_globals + "-"'
#
#     tainted_string = taint_pyobject(
#         code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
#     )
#     mod.pt_exec_with_globals(tainted_string)
#
#     data = _get_iast_data()
#
#     assert len(data["vulnerabilities"]) == 1
#     vulnerability = data["vulnerabilities"][0]
#     source = data["sources"][0]
#     assert vulnerability["type"] == VULN_CODE_INJECTION
#     assert source["name"] == "path"
#     assert source["origin"] == OriginType.PATH
#     assert source["value"] == '"abc" + "def"'
#     assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": '"abc" + "def"'}]
#     assert "value" not in vulnerability["evidence"].keys()
#     assert vulnerability["evidence"].get("pattern") is None
#     assert vulnerability["evidence"].get("redacted") is None


def test_code_injection_literal_eval(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")
    code_string = "[1, 2, 3]"

    tainted_string = taint_pyobject(
        code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
    )
    mod.pt_literal_eval(tainted_string)

    data = _get_span_report()

    assert data is None
