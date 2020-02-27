import os

import pytest

# TODO
# I find it odd that this code does not exist yet somewhere.
# Could be useful for others. Move out to a module.
@pytest.fixture
def environ_override():
    _EMPTY = object()

    original_env = {}

    def _environ_override(var, value):
        original_env.setdefault(var, os.environ.get(var, _EMPTY))
        os.environ[var] = value

    yield _environ_override

    for var, value in original_env.items():
        if value is _EMPTY:
            del os.environ[var]
        else:
            os.environ[var] = value
