from __future__ import print_function

from ddtrace import tracer

from nose.tools import eq_

if __name__ == '__main__':
    eq_(tracer.writer.api.hostname, "172.10.0.1")
    eq_(tracer.writer.api.port, 58126)
    print("Test success")
