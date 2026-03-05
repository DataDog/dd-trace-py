# Demo: intentional lints for pre-commit ruff --fix showcase. Safe to remove.
"""Minimal file with fixable ruff violations for PR demonstration."""

import os  # noqa: F401
import sys  # noqa: F401


def foo():
    x = 1
    return x


def bar():
    y = 2
    return y
