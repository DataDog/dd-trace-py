from ddtrace import tracer


if __name__ == "__main__":
    assert tracer._tags.get("env") == "test"
    print("Test success")
