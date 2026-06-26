# ddtrace/_monkey.py and the integration registry expect all integrations
# to define get_version in <integration>/patch.py.
# The actual pytest plugin lives at ddtrace.testing.internal.pytest.
def get_version() -> str:
    import importlib.metadata as importlib_metadata

    return str(importlib_metadata.version("pytest"))


def _supported_versions() -> dict[str, str]:
    return {"pytest": ">=7.4.4"}
