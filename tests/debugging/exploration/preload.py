import os
import sys


# Make the tests/debugging folder available as a module. We don't want to
# go as further back as tests/ because we are likely to override a folder with
# a similar name from the target project. That's because we install the
# frameworks in edit mode and run the test suite from their root folder, which
# is likely to contain a tests/ folder. Hence, if we insert our our tests/
# folder we risk breaking the framework's tests; whereas if we append it, we
# risk breaking our exploration tests.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


import _coverage  # noqa
import _profiler  # noqa


print("Enabling debugging exploration testing")
