# ddtrace/_monkey.py expects all integrations to define get_version in <integration>/patch.py file
def get_version():
    # type: () -> str
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    return str(importlib_metadata.version("pytest-bdd"))
