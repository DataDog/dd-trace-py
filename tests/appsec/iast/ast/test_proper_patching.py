# -*- encoding: utf-8 -*-
import pytest


try:
    from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
    from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
    from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.ast.fixtures.misleading_methods")


class TestProperMethodsReplacement(BaseReplacement):
    def test_string_proper_join_called(self):
        """
        Join should not be replaced by the aspect since it's not the builtin method
        """
        input_str = "foo"

        string_argument1 = create_taint_range_with_format(":+-b-+:")

        result = mod.FakeStr(input_str).call_join(input_str, string_argument1)

        assert as_formatted_evidence(result) == "not_tainted"

    def test_string_proper_ljust_called(self):
        """
        Join should not be replaced by the aspect since it's not the builtin method
        """
        input_str = "foo"

        string_argument1 = create_taint_range_with_format(":+-b-+:")

        result = mod.FakeStr(input_str).call_ljust(input_str, string_argument1)

        assert as_formatted_evidence(result) == "not_tainted"
