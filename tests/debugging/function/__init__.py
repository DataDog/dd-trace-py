import sys

import pytest


if sys.version_info[:2] == (3, 12):
    pytest.skip("Dynamic instrumentation is not supported with Python 3.12", allow_module_level=True)
