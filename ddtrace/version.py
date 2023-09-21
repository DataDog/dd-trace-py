def get_version():
    # type: () -> str
    try:
        from ._version import version

        return version
    except ImportError:
        # package is not installed
        return "dev"
