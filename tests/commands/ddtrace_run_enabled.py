from ddtrace import tracer

from nose.tools import ok_

if __name__ == '__main__':
    ok_(tracer.enabled)
    print("Test success")
