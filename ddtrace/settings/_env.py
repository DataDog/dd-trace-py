from collections.abc import MutableMapping
import os
import typing as t


class EnvConfig(MutableMapping):
    """
    Wrapper around os.environ that validates the variable against list of allowed variables.

    It is used to get and set environment variables in a safe way mimicking the behavior of os.environ.
    """

    def __init__(self):
        self._env = os.environ

    def __getitem__(self, key):
        # Check will go here to validate the variable against list of allowed variables

        return self._env[key]

    def __setitem__(self, key, value):
        self._env[key] = value

    def __delitem__(self, key):
        del self._env[key]

    def __iter__(self):
        return iter(self._env)

    def __len__(self):
        return len(self._env)


environ = EnvConfig()
"""
Equivalent to os.environ using the EnvConfig wrapper-class.
"""


def get_env(env_name: str, default: t.Any = None) -> t.Any:
    """
    Get an environment variable.
    If the variable is a DD_ or OTEL_ or _DD prefixed variable, it will be validated against list of allowed variables.

    This is a wrapper around os.environ.get(env_name, default). as direct access to os.environ is not allowed.
    """
    return environ.get(env_name, default)


def set_env(env_name: str, value: t.Any) -> None:
    """
    Set an environment variable.
    This is a wrapper around os.environ[env_name] = value. as direct access to os.environ is not allowed.
    """
    os.environ[env_name] = value
