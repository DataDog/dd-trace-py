from ddtrace.internal.packages import get_version_for_package


# ddtrace/_monkey.py expects all integrations to define get_version in <integration>/patch.py file
def get_version():
    # type: () -> str
    return get_version_for_package("pytest-bdd")
