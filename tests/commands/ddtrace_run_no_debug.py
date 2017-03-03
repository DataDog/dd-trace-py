from __future__ import print_function

from ddtrace import tracer

from nose.tools import ok_

if __name__ == '__main__':
    ok_(not tracer.debug_logging)
    print("Test success")
