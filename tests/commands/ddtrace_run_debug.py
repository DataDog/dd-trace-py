from ddtrace import tracer

from nose.tools import ok_

if __name__ == '__main__':
    ok_(tracer.debug_logging)
    print("Test success")
