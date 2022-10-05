# -*- encoding: utf-8 -*-
"""
An API to provide atexit functionalities
"""
from __future__ import absolute_import

import atexit
import logging
import typing


log = logging.getLogger(__name__)


if hasattr(atexit, "unregister"):
    register = atexit.register
    unregister = atexit.unregister
else:
    # Hello Python 2!
    _registry = []  # type: typing.List[typing.Tuple[typing.Callable[..., None], typing.Tuple, typing.Dict]]

    def _ddtrace_atexit():
        # type: (...) -> None
        """Wrapper function that calls all registered function on normal program termination"""
        global _registry

        # DEV: we make a copy of the registry to prevent hook execution from
        # introducing new hooks, potentially causing an infinite loop.
        for hook, args, kwargs in list(_registry):
            try:
                hook(*args, **kwargs)
            except Exception:
                # Mimic the behaviour of Python's atexit hooks.
                log.exception("Error in atexit hook %r", hook)

    def register(
        func,  # type: typing.Callable[..., typing.Any]
        *args,  # type: typing.Any
        **kwargs  # type: typing.Any
    ):
        # type: (...) -> typing.Callable[..., typing.Any]
        """Register a function to be executed upon normal program termination"""

        _registry.append((func, args, kwargs))
        return func

    def unregister(func):
        # type: (typing.Callable[..., typing.Any]) -> None
        """
        Unregister an exit function which was previously registered using
        atexit.register.
        If the function was not registered, it is ignored.
        """
        global _registry

        _registry = [(f, args, kwargs) for f, args, kwargs in _registry if f != func]

    atexit.register(_ddtrace_atexit)
