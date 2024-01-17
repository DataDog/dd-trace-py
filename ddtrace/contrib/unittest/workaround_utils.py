import sys
import traceback
import unittest

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def is_flexmock_erroneous_add_success(instance: unittest.TextTestResult) -> bool:
    """Returns True if we should work around flexmock's bug of always calling
    addSuccess regardless of the success of the original test.

    We double-check that we are, in fact, in flexmock's codebase, just in case.

    See https://github.com/flexmock/flexmock/issues/150
    """
    # If flexmock is not loaded, then it can't be an erroneous success
    if "flexmock" not in sys.modules:
        return False

    # If the attribute exists and is True, then it's not an erroneous success
    if getattr(instance, "_pre_flexmock_success", False):
        return False

    # If we're somewhere in flexmock's codebase, then it is an erroneous success
    current_stack = traceback.extract_stack()

    for frame in current_stack:
        # We ensure that we're in run's stopTest() method, because in flexmock 0.11.x, the frame name is "decorated"
        # which is potentially ambiguous since that name is used in both the wrapper for addSuccess and for stopTest
        if frame.name == "run" and frame.filename.endswith("case.py"):
            if "stopTest" in frame.line:
                return True

    return False
