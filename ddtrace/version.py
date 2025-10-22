def get_version() -> str:
    try:
        from ._version import version

        return version
    except ImportError:
        from importlib.metadata import version as ilm_version

        try:
            return ilm_version("ddtrace")
        except ModuleNotFoundError:
            # package is not installed
            return "dev"
