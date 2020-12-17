from ddtrace import tracer

if __name__ == "__main__":
    assert tracer.writer._hostname == "172.10.0.1"
    assert tracer.writer._port == 8120
    print("Test success")
