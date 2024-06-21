# Description: Test the crashtracker module

import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker():
    assert False
