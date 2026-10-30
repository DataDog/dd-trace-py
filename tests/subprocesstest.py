"""Compatibility names for the shared subprocess test runner."""

from unittest import TestCase

import pytest


def run_in_subprocess(*args, env_overrides=None):
    return pytest.mark.subprocess(*args, env=env_overrides or {})


class SubprocessTestCase(TestCase):
    run_in_subprocess = staticmethod(run_in_subprocess)
