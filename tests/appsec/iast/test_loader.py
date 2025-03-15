#!/usr/bin/env python3

import importlib
import sys
from unittest import mock

import ddtrace.appsec._iast._loader
import ddtrace.bootstrap.preload
from ddtrace.settings.asm import config as asm_config


ASPECTS_MODULE = "ddtrace.appsec._iast._taint_tracking.aspects"


def test_patching_error():
    """
    When IAST is enabled and the loader fails to compile the module,
    the module should still be imported successfully.
    """
    fixture_module = "tests.appsec.iast.fixtures.loader"
    asm_config_orig_value = asm_config._iast_enabled
    try:
        asm_config._iast_enabled = True

        if fixture_module in sys.modules:
            del sys.modules[fixture_module]

        if ASPECTS_MODULE in sys.modules:
            del sys.modules[ASPECTS_MODULE]

        ddtrace.appsec._iast._loader.IS_IAST_ENABLED = True

        with mock.patch("ddtrace.appsec._iast._loader.compile", side_effect=ValueError) as loader_compile:
            importlib.reload(ddtrace.bootstrap.preload)
            imported_fixture_module = importlib.import_module(fixture_module)

            imported_fixture_module.add(2, 1)
            loader_compile.assert_called_once()
            assert ASPECTS_MODULE not in sys.modules

    finally:
        asm_config._iast_enabled = asm_config_orig_value
