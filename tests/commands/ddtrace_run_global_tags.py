from ddtrace import tracer


if __name__ == "__main__":
    assert tracer._tags.get("a") == "True"
    assert tracer._tags.get("b") == "0"
    assert tracer._tags.get("c") == "C"
    print("Test success")
