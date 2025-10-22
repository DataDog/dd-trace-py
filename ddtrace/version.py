def get_version() -> str:
    return "4.0.0.dev0"
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
