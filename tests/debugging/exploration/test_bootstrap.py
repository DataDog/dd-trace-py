import os

import pytest

from ddtrace.internal.compat import PY2


if PY2:
    OUT = """Enabling debugging exploration testing
========================== LineCoverage: probes stats ==========================

Installed probes: 0/0

================================ Line coverage =================================

Source                                                       Lines Covered
==========================================================================
No lines found
===================== DeterministicProfiler: probes stats ======================

Installed probes: 0/0

============================== Function coverage ===============================

No functions called
"""
else:
    OUT = """Enabling debugging exploration testing
===================== DeterministicProfiler: probes stats ======================

Installed probes: 0/0

============================== Function coverage ===============================

No functions called
========================== LineCoverage: probes stats ==========================

Installed probes: 0/0

================================ Line coverage =================================

Source                                                       Lines Covered
==========================================================================
No lines found
"""

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env():
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    return environ


@pytest.mark.subprocess(out=OUT, env=_build_env(), run_module=True)
def test_exploration_bootstrap():
    # We test that we get the expected output from the exploration debuggers
    # and no errors when running the sitecustomize.py script.
    pass
