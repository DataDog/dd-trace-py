import pytest

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.internal.module import is_module_installed
from tests.appsec.iast.conftest import iast_context_defaults  # noqa: F401
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.conftest import _get_span_report
from tests.utils import override_env


FIXTURES_PATH = "tests/appsec/integrations/fixtures/patch_langchain.py"
FIXTURES_MODULE = "tests.appsec.integrations.fixtures.patch_langchain"

with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


@pytest.mark.skipif(not is_module_installed("langchain"), reason="Langchain tests work on 3.9 or higher")
def test_openai_llm_appsec_iast_cmdi(iast_context_defaults):  # noqa: F811
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

    span_report = _get_span_report()
    assert span_report
    data = span_report.build_and_scrub_value_parts()
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"source": 0, "value": "echo "},
        {"pattern": "", "redacted": True, "source": 0},
        {"source": 0, "value": "Hello World"},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == "test_openai_llm_appsec_iast_cmdi"
    assert source["origin"] == OriginType.PARAMETER
    assert "value" not in source.keys()

    line, hash_value = get_line_and_hash("test_openai_llm_appsec_iast_cmdi", VULN_CMDI, filename=FIXTURES_PATH)
    assert vulnerability["location"]["path"] == FIXTURES_PATH
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == "patch_langchain"
    assert vulnerability["location"]["class"] == ""
    assert vulnerability["hash"] == hash_value
