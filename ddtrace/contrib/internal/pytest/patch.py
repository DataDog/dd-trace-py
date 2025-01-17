# Get version is imported from patch.py in _monkey.py
def get_version():
    # type: () -> str
    import pytest

    return pytest.__version__
