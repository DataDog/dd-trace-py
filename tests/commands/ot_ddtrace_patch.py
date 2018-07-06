from __future__ import print_function

from nose.tools import ok_

import opentracing
from ddtrace.opentracer import Tracer

if __name__ == '__main__':
    ok_(isinstance(opentracing.tracer, Tracer))
    print("Test success")
