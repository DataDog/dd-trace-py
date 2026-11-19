#!/usr/bin/env python3

import importlib
import sys
from unittest import mock

import pytest

import ddtrace.appsec._iast._loader
from ddtrace.internal.iast.product import post_preload
from ddtrace.internal.settings.asm import config as asm_config


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
            post_preload()
            imported_fixture_module = importlib.import_module(fixture_module)

            imported_fixture_module.add(2, 1)
            loader_compile.assert_called_once()
            assert ASPECTS_MODULE not in sys.modules

    finally:
        asm_config._iast_enabled = asm_config_orig_value


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Lazy imports require Python 3.15")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES="iast_lazy_importer,tests.appsec."),
    parametrize=dict(_DD_TEST_LAZY_IMPORT=("import", "from", "modules_import", "modules_from")),
    err=None,
)
def test_lazy_import_instruments_module_on_first_use():
    """Deferred dependencies must receive AST instrumentation when first resolved."""
    import importlib
    import os
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
    from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges

    imports = {
        "import": ("lazy import tests.appsec.iast.fixtures.loader as target", "target.add"),
        "from": ("lazy from tests.appsec.iast.fixtures.loader import add", "add"),
        "modules_import": (
            '__lazy_modules__ = ["tests.appsec.iast.fixtures.loader"]\n'
            "import tests.appsec.iast.fixtures.loader as target",
            "target.add",
        ),
        "modules_from": (
            '__lazy_modules__ = ["tests.appsec.iast.fixtures.loader"]\n'
            "from tests.appsec.iast.fixtures.loader import add",
            "add",
        ),
    }
    statement, transform = imports[os.environ["_DD_TEST_LAZY_IMPORT"]]

    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "iast_lazy_importer.py").write_text(
            f'{statement}\n\ndef run(value):\n    return "caller:" + {transform}("target:", value)\n'
        )
        sys.path.insert(0, directory)
        importer = importlib.import_module("iast_lazy_importer")
        assert "tests.appsec.iast.fixtures.loader" not in sys.modules

        assert _iast_start_request() is not None
        try:
            value = taint_pyobject("untrusted", "parameter", "untrusted", OriginType.PARAMETER)
            for _ in range(2):
                result = importer.run(value)
                assert result == "caller:target:untrusted"
                assert "tests.appsec.iast.fixtures.loader" in sys.modules
                ranges = get_tainted_ranges(result)
                assert len(ranges) == 1
                assert ranges[0].start == len("caller:target:")
                assert ranges[0].length == len("untrusted")
                assert ranges[0].source.name == "parameter"
                assert ranges[0].source.value == "untrusted"
                assert ranges[0].source.origin == OriginType.PARAMETER
        finally:
            _iast_finish_request()
            sys.path.remove(directory)
