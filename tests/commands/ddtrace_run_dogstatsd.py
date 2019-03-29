from __future__ import print_function

from ddtrace import tracer

if __name__ == '__main__':
    assert tracer.dogstatsd.host == "172.10.0.1"
    assert tracer.dogstatsd.port == 8120
    print("Test success")
