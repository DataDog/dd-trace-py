from typing import Dict


# Get version is imported from patch.py in _monkey.py
def get_version():
    # type: () -> str
    import pytest

    return pytest.__version__


def _supported_versions() -> Dict[str, str]:
    return {"pytest": ">=6.0"}
