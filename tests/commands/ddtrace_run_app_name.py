from __future__ import print_function

from ddtrace.opentracer import Tracer

if __name__ == '__main__':
    tracer = Tracer()
    print(tracer._service_name)
