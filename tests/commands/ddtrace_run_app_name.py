from __future__ import print_function

from ddtrace.opentracer import Tracer

from nose.tools import ok_

if __name__ == '__main__':
    tracer = Tracer()
    ok_(tracer._service_name)
    print("Test success")
