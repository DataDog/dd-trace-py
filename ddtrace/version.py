def get_version():
    # type: () -> str
    try:
        from ._version import version

        return version
    except Exception:
        try:
            # something went wrong while creating _version.py, let's fallback to pkg_resources
            import pkg_resources

            return pkg_resources.get_distribution(__name__).version
        except Exception:
            # package is not installed
            return "dev"
