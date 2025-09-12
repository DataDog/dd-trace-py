from __future__ import print_function

from ddtrace.trace import tracer


if __name__ == "__main__":
    # check both configurations with host:port or unix socket
    if tracer._span_aggregator.writer.dogstatsd.socket_path is None:
        assert tracer._span_aggregator.writer.dogstatsd.host == "172.10.0.1"
        assert tracer._span_aggregator.writer.dogstatsd.port == 8120
    else:
        assert tracer._span_aggregator.writer.dogstatsd.socket_path.endswith("dogstatsd.sock")
    print("Test success")
