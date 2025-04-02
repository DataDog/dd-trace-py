# ddtrace/_monkey.py expects all integrations to define get_version in <integration>/patch.py file
def get_version():
    # type: () -> str
    from importlib.metadata import version

    return str(version("pytest-bdd"))
