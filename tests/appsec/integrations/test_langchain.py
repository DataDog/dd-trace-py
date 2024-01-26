import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.internal import core
from ddtrace.internal.module import is_module_installed
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.conftest import iast_span_defaults  # noqa: F401
from tests.appsec.iast.iast_utils import get_line_and_hash


FIXTURES_PATH = "tests/appsec/integrations/fixtures/patch_langchain.py"
FIXTURES_MODULE = "tests.appsec.integrations.fixtures.patch_langchain"


@pytest.mark.skipif(not is_module_installed("langchain"), reason="Langchain tests work on 3.9 or higher")
def test_openai_llm_appsec_iast_cmdi(iast_span_defaults):  # noqa: F811
    mod = _iast_patched_module(FIXTURES_MODULE)
    string_to_taint = "I need to use the terminal tool to print a Hello World"
    prompt = taint_pyobject(
        pyobject=string_to_taint,
        source_name="test_openai_llm_appsec_iast_cmdi",
        source_value=string_to_taint,
        source_origin=OriginType.PARAMETER,
    )
    res = mod.patch_langchain(prompt)
    assert res == "4"

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report

    vulnerability = list(span_report.vulnerabilities)[0]
    source = span_report.sources[0]
    assert vulnerability.type == VULN_CMDI
    assert vulnerability.evidence.valueParts == [
        {"value": "echo Hello World", "source": 0},
    ]
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None
    assert source.name == "test_openai_llm_appsec_iast_cmdi"
    assert source.origin == OriginType.PARAMETER
    assert source.value == string_to_taint

    line, hash_value = get_line_and_hash("test_openai_llm_appsec_iast_cmdi", VULN_CMDI, filename=FIXTURES_PATH)
    assert vulnerability.location.path == FIXTURES_PATH
    assert vulnerability.location.line == line
    assert vulnerability.hash == hash_value
