#!/usr/bin/env python3

import importlib
import sys

import mock

from tests.utils import override_env


def setup():
    import ddtrace.appsec._iast._loader

    ddtrace.appsec._iast._loader.IS_IAST_ENABLED = True


def test_patching_error():
    """
    When IAST is enabled and the loader fails to compile the module,
    the module should still be imported successfully.
    """

    with override_env({"DD_IAST_ENABLED": "true"}), mock.patch(
        "ddtrace.appsec._iast._loader.compile", side_effect=ValueError
    ):
        importlib.import_module("tests.appsec.iast.fixtures.loader")

        assert "ddtrace.appsec._iast._taint_tracking.aspects" not in sys.modules.keys()
