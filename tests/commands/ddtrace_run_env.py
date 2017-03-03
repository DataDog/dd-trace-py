from __future__ import print_function

import os
from ddtrace import tracer

from nose.tools import eq_

if __name__ == '__main__':
    eq_(tracer.tags["env"], "test")
    print("Test success")
