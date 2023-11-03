def get_version():
    # type: () -> str
    try:
        from ._version import version

        return version
    except ImportError:
        try:
            # something went wrong while creating _version.py, let's fallback to importlib
            from importlib.metadata import PackageNotFoundError
            from importlib.metadata import version
        except ImportError:
            # required for python3.7
            from importlib_metadata import PackageNotFoundError  # noqa
            from importlib_metadata import version  # type: ignore[no-redef]
        try:
            version(__name__)
        except PackageNotFoundError:
            # package is not installed
            return "dev"
