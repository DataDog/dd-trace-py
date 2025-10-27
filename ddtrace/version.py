def get_version() -> str:
    try:
        from ._version import version

        return version
    except ImportError:
        from importlib.metadata import version as ilm_version

        try:
            version = ilm_version("ddtrace")
            print(version)
            return version
        except ModuleNotFoundError:
            # package is not installed
            return "dev"
