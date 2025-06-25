import os

import pytest

from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast.constants import VULN_XSS
from ddtrace.appsec._iast.taint_sinks.xss import XSS
from tests.appsec.iast.taint_sinks._taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks._taint_sinks_utils import get_parametrize
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize(
    "evidence_input, sources_expected, vulnerabilities_expected,element", list(get_parametrize(VULN_XSS))
)
def test_xss_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_context_defaults, element
):
    tainted_object = evidence_input_value = evidence_input.get("value", "")
    if evidence_input_value:
        tainted_object = _taint_pyobject_multiranges(
            evidence_input_value,
            [
                (
                    input_ranges["iinfo"]["parameterName"],
                    input_ranges["iinfo"]["parameterValue"],
                    str_to_origin(input_ranges["iinfo"]["type"]),
                    input_ranges["start"],
                    input_ranges["end"] - input_ranges["start"],
                )
                for input_ranges in evidence_input.get("ranges", {})
            ],
        )

    XSS.report(tainted_object)

    data = _get_iast_data()
    vulnerability = list(data["vulnerabilities"])[0]
    source = list(data["sources"])[0]
    source["origin"] = origin_to_str(source["origin"])

    assert vulnerability["type"] == VULN_XSS
    assert vulnerability["evidence"] == vulnerabilities_expected["evidence"]
    assert source == sources_expected
