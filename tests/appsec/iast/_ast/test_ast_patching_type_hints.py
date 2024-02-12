# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec._iast._utils import _get_patched_code


@pytest.mark.skipif(sys.version_info <= (3, 8, 0), reason="Sample code not compatible with Python 3.7")
def test_no_index_aspects_py38plus():
    """
    Methods should not be replaced by the aspect since it's not the builtin method
    """
    patched_code = _get_patched_code("tests/appsec/iast/_ast/fixtures/annotated_code.py", "annotated_code")

    # Makes sure no subscripts are patched:
    assert "index_aspect" not in patched_code

    # Makes sure all add operators are patched:
    assert "ddtrace_aspects.add_aspect" in patched_code
    assert "+" not in patched_code


def test_no_index_aspects_py37():
    """
    Methods should not be replaced by the aspect since it's not the builtin method
    """
    patched_code = _get_patched_code("tests/appsec/iast/_ast/fixtures/annotated_code37.py", "annotated_code")

    # Makes sure no subscripts are patched:
    assert "index_aspect" not in patched_code

    # Makes sure all add operators are patched:
    assert "ddtrace_aspects.add_aspect" in patched_code
    assert "+" not in patched_code
