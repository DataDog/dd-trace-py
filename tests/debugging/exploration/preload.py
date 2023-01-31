import os
import sys


# Make the tests/debugging folder available as a module. We don't want to
# go as further back as tests/ because we are likely to override a folder with
# a similar name from the target project.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


import _coverage  # noqa
import _profiler  # noqa


print("Enabling debugging exploration testing")
