"""A stand-in for an instrumented third-party module, patched by dotted name in test_hooks.py."""

import os


class Target:
    def method(self, value):
        return value * 2


def function(value):
    return value + 1


def fork_and_return_pid():
    """Forks inside the wrapped call, so both processes unwind through the context's __return__."""
    return os.fork()
