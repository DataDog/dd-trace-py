from ddtrace import tracer

from nose.tools import eq_

if __name__ == '__main__':
    eq_(tracer.writer.api.hostname, "172.10.0.1")
    eq_(tracer.writer.api.port, 8120)
    print("Test success")
