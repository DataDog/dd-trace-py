#!/usr/bin/env python3

import importlib
import sys

import mock

import ddtrace.appsec._iast._loader
import ddtrace.bootstrap.preload
from tests.utils import override_env


ASPECTS_MODULE = "ddtrace.appsec._iast._taint_tracking.aspects"


def test_patching_error():
    """
    When IAST is enabled and the loader fails to compile the module,
    the module should still be imported successfully.
    """
    fixture_module = "tests.appsec.iast.fixtures.loader"
    if fixture_module in sys.modules:
        del sys.modules[fixture_module]

    if ASPECTS_MODULE in sys.modules:
        del sys.modules[ASPECTS_MODULE]

    ddtrace.appsec._iast._loader.IS_IAST_ENABLED = True

    with override_env({"DD_IAST_ENABLED": "true"}), mock.patch(
        "ddtrace.appsec._iast._loader.compile", side_effect=ValueError
    ) as loader_compile, mock.patch("ddtrace.appsec._iast._loader.exec") as loader_exec:
        importlib.reload(ddtrace.bootstrap.preload)
        imported_fixture_module = importlib.import_module(fixture_module)

        imported_fixture_module.add(2, 1)
        loader_compile.assert_called_once()
        loader_exec.assert_not_called()
        assert ASPECTS_MODULE not in sys.modules
