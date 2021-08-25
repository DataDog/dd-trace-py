def get_version():
    # type: () -> str
    try:
        from ._version import version

        return version
    except ImportError:
        try:
            # something went wrong while creating _version.py, let's fallback to pkg_resources
            import pkg_resources

            return pkg_resources.get_distribution(__name__).version
        except pkg_resources.DistributionNotFound:
            # package is not installed
            return "dev"
