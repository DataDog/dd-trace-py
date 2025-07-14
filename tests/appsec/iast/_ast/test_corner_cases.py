import sys

import pytest

from tests.appsec.iast.iast_utils import _iast_patched_module


@pytest.mark.skipif(sys.version_info < (3, 9), reason="This test doesn't raises an error on 3.8")
def test_string_proper_method_called():
    """Fixed in ddtrace/appsec/_iast/_loader.py except TypeError:"""
    with pytest.raises(TypeError):
        mod = _iast_patched_module("tests.appsec.iast._ast.fixtures.del_variables_at_runtime")
        assert mod
