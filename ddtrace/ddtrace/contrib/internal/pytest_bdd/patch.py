# ddtrace/_monkey.py expects all integrations to define get_version in <integration>/patch.py file
def get_version() -> str:
    import importlib.metadata as importlib_metadata

    return str(importlib_metadata.version("pytest-bdd"))


def _supported_versions() -> dict[str, str]:
    return {"pytest_bdd": "*"}
