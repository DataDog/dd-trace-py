from ddtrace import _monkey


if __name__ == "__main__":
    assert "redis" in _monkey._get_patched_modules()
    print("Test success")
