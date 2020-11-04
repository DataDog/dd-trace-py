from ddtrace import tracer

if __name__ == "__main__":
    assert tracer.writer.url == "http://172.10.0.1:8120"
    print("Test success")
