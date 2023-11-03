def get_version():
    # type: () -> str
    try:
        from ._version import version

        return version
    except ImportError:
        try:
            # something went wrong while creating _version.py, let's fallback to importlib
            from importlib.metadata import PackageNotFoundError
            from importlib.metadata import version as ilm_version
        except ImportError:
            # required for python3.7
            from importlib_metadata import PackageNotFoundError  # type: ignore[no-redef]  # noqa
            from importlib_metadata import version as ilm_version  # type: ignore[no-redef]
        try:
            return ilm_version(__name__)
        except PackageNotFoundError:
            # package is not installed
            return "dev"
