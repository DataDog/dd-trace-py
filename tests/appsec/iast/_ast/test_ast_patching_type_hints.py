# -*- encoding: utf-8 -*-
from ddtrace.appsec._iast._utils import _get_patched_code


def test_no_index_aspects():
    """
    Methods should not be replaced by the aspect since it's not the builtin method
    """
    patched_code = _get_patched_code("tests/appsec/iast/_ast/fixtures/annotated_code.py", "annotated_code")

    # Makes sure no subscripts are patched:
    assert "index_aspect" not in patched_code

    # Makes sure all add operators are patched:
    assert "ddtrace_aspects.add_aspect" in patched_code
    assert "+" not in patched_code
