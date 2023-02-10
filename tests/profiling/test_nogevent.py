import os

import pytest
import six

from ddtrace.internal import nogevent


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


@pytest.mark.skipif(not TESTING_GEVENT or six.PY3, reason="Not testing gevent or testing on Python 3")
def test_nogevent_rlock():
    import gevent

    assert not isinstance(nogevent.RLock()._RLock__block, gevent.thread.LockType)
