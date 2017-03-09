from __future__ import print_function

import os
from ddtrace import tracer

from nose.tools import eq_

if __name__ == '__main__':
    eq_(os.environ['DATADOG_SERVICE_NAME'], 'python')
    print("Test success")
