from __future__ import print_function

from nose.tools import eq_

import opentracing

if __name__ == '__main__':
    eq_(opentracing.tracer._service_name, "svc")
    print("Test success")
