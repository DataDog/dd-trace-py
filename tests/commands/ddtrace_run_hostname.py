from ddtrace import tracer


if __name__ == "__main__":
    assert tracer.writer.agent_url == "http://172.10.0.1:8120"
    print("Test success")
