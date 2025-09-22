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
        """Handles: environ[key]"""
        # TODO: Check will go here to validate the variable against list of allowed variables

        return self._env[key]

    def get(self, key, default=None):
        """
        Get an environment variable.
        If the variable is a DD_ or OTEL_ or _DD prefixed variable, it will be validated against list
        of allowed variables.

        This is a wrapper around os.environ.get(key, default). as direct access to os.environ is not allowed.
        """
        # TODO: Check will go here to validate the variable against list of allowed variables

        return self._env.get(key, default)

    def __setitem__(self, key, value):
        """Handles: environ[key] = value"""
        self._env[key] = value

    def __delitem__(self, key):
        """Handles: del environ[key]"""
        del self._env[key]

    def __contains__(self, key):
        """Handles: key in environ"""
        # TODO: Check will go here to validate the variable against list of allowed variables

        return key in self._env

    def __iter__(self):
        """Handles: for key in environ"""
        # TODO: Check will go here to filter out unknown variables against list of allowed variables

        return iter(self._env)

    def __len__(self):
        """Handles: len(environ)"""
        return len(self._env)

    def items(self):
        """Handles: environ.items()"""
        # TODO: Check will go here to filter out unknown variables against list of allowed variables

        return self._env.items()

    def keys(self):
        """Handles: environ.keys()"""
        # TODO: Check will go here to filter out unknown variables against list of allowed variables

        return self._env.keys()

    def values(self):
        """
        Get all the values of the environment variables.
        If the variable is a DD_ or OTEL_ or _DD prefixed variable, it will be validated against list of allowed
        variables.

        This is a wrapper around os.environ.values(). as direct access to os.environ is not allowed.
        """
        # TODO: Check will go here to filter out unknown variables against list of allowed variables

        return self._env.values()


environ = EnvConfig()
"""
Equivalent to os.environ using the EnvConfig wrapper-class.
"""


def getenv(env_name: str, default: t.Any = None) -> t.Any:
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
