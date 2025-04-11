from ddtrace import _monkey
from ddtrace.trace import tracer


if __name__ == "__main__":
    assert not tracer.enabled
    assert len(_monkey._get_patched_modules()) == 0
    print("Test success")
