from ddtrace import monkey
from ddtrace import tracer


if __name__ == "__main__":
    assert not tracer.enabled
    assert len(monkey.get_patched_modules()) == 0
    print("Test success")
