from ddtrace.internal import crashtracker

import pytest
import sys

@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux only')
def test_crashtracker():
    assert False
