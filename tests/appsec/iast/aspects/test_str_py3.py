# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec.iast._taint_tracking import as_formatted_evidence
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods_py3")


class TestOperatorsReplacement(BaseReplacement):
    @staticmethod
    def test_taint():  # type: () -> None
        string_input = "foo"
        assert as_formatted_evidence(string_input) == "foo"

        string_input = create_taint_range_with_format(":+-foo-+:")
        assert as_formatted_evidence(string_input) == ":+-foo-+:"

    @pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python >=3.6 only")
    def test_string_build_string_tainted(self):  # type: () -> None
        string_input = create_taint_range_with_format(":+-foo-+:")
        result = mod.do_fmt_value(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == ":+-foo     bar-+:"

    def test_string_format_tainted(self):  # type: () -> None
        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod.do_repr_fstring(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == ":+-foo       -+:"
