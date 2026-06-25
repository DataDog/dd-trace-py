"""Version-gated collection for the wrapping test suite.

Test files that use syntax introduced in a specific Python version would raise a
``SyntaxError`` *at import time* on older interpreters, before any ``skipif`` could
run. So we skip collecting them here, mirroring
``tests/appsec/iast/aspects/conftest.py``.

The ``test_*_py<major><minor>.py`` naming convention is made executable below: each
such file is parsed for its version suffix and ignored on anything older, so adding
a new gated file needs no edit here (only the matching ruff exclude in pyproject.toml,
which cannot run Python logic).
"""

import os
import re
import sys


_VERSION_SUFFIX = re.compile(r"_py(\d)(\d+)\.py$")

collect_ignore = []
for _name in os.listdir(os.path.dirname(__file__)):
    _match = _VERSION_SUFFIX.search(_name)
    if _match and sys.version_info < (int(_match.group(1)), int(_match.group(2))):
        collect_ignore.append(_name)
