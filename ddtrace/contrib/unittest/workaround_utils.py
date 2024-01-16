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
    # If the attribute exists and is True, then it's not an erroneous success
    if getattr(instance, "_pre_flexmock_success", False):
        return False

    # If we're somewhere in flexmock's codebase, then it is an erroneous success
    current_stack = traceback.extract_stack()
    for frame in current_stack:
        # We take a more restrictive approach that may require updating if flexmock's codebase changes, but avoids
        # false positives
        if frame.name == "stopTest" and frame.filename.endswith("flexmock.py"):  # flexmoc, 0.10.10 and below
            return True
        if frame.name == "decorated" and frame.filename.endswith("_integrations.py"):  # flexmock 0.11 and above
            return True

    return False
