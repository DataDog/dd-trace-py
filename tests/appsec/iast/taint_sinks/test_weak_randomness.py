import sys

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast.constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS
from ddtrace.appsec._iast.constants import VULN_WEAK_RANDOMNESS
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash


FIXTURES_RANDOM_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_randomness_random.py"
FIXTURES_RANDOM_MODULE_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_randomness_random_module.py"
FIXTURES_RANDOM_SECURE_MODULE_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_randomness_random_secure_module.py"
FIXTURES_SECRETS_PATH = "tests/appsec/iast/fixtures/taint_sinks/weak_randomness_secrets.py"

WEEK_RANDOMNESS_PY_VERSION = not ((3, 9, 0) <= sys.version_info)


@pytest.mark.skipif(WEEK_RANDOMNESS_PY_VERSION, reason="Some random methods exists on 3.9 or higher")
@pytest.mark.parametrize(
    "random_func",
    DEFAULT_WEAK_RANDOMNESS_FUNCTIONS,
)
def test_weak_randomness(random_func, iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.weak_randomness_random")

    getattr(mod, "random_{}".format(random_func))()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash(
        "weak_randomness_{}".format(random_func), VULN_WEAK_RANDOMNESS, filename=FIXTURES_RANDOM_PATH
    )
    vulnerability = list(span_report.vulnerabilities)[0]
    assert span_report.sources == []
    assert vulnerability.type == VULN_WEAK_RANDOMNESS
    assert vulnerability.location.path == FIXTURES_RANDOM_PATH
    assert vulnerability.location.line == line
    assert vulnerability.hash == hash_value
    assert vulnerability.evidence.value == "Random.{}".format(random_func)
    assert vulnerability.evidence.valueParts is None


@pytest.mark.skipif(WEEK_RANDOMNESS_PY_VERSION, reason="Some random methods exists on 3.9 or higher")
def test_weak_randomness_no_dynamic_import(iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.weak_randomness_random")

    mod.random_dynamic_import()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report is None


@pytest.mark.skipif(WEEK_RANDOMNESS_PY_VERSION, reason="Some random methods exists on 3.9 or higher")
@pytest.mark.parametrize(
    "random_func",
    DEFAULT_WEAK_RANDOMNESS_FUNCTIONS,
)
def test_weak_randomness_module(random_func, iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.weak_randomness_random_module")

    getattr(mod, "random_{}".format(random_func))()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash(
        "weak_randomness_{}".format(random_func), VULN_WEAK_RANDOMNESS, filename=FIXTURES_RANDOM_MODULE_PATH
    )
    vulnerability = list(span_report.vulnerabilities)[0]
    assert span_report.sources == []
    assert vulnerability.type == VULN_WEAK_RANDOMNESS
    assert vulnerability.location.path == FIXTURES_RANDOM_MODULE_PATH
    assert vulnerability.location.line == line
    assert vulnerability.hash == hash_value
    assert vulnerability.evidence.value == "Random.{}".format(random_func)
    assert vulnerability.evidence.valueParts is None


@pytest.mark.skipif(WEEK_RANDOMNESS_PY_VERSION, reason="Some random methods exists on 3.9 or higher")
@pytest.mark.parametrize(
    "random_func",
    DEFAULT_WEAK_RANDOMNESS_FUNCTIONS,
)
def test_weak_randomness_secure_module(random_func, iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.weak_randomness_random_secure_module")

    getattr(mod, "random_{}".format(random_func))()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report is None


@pytest.mark.skipif(WEEK_RANDOMNESS_PY_VERSION, reason="Some random methods exists on 3.9 or higher")
def test_weak_randomness_secrets_secure_package(iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.weak_randomness_secrets")

    mod.random_choice()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report is None
