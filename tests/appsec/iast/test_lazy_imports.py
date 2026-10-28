import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Lazy imports require Python 3.15")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_IAST_ENABLED="true", _DD_IAST_PATCH_MODULES="iast_lazy_importer,iast_lazy_target"),
    parametrize=dict(_DD_TEST_LAZY_IMPORT=("import", "from", "modules_import", "modules_from")),
    err=None,
)
def test_iast_lazy_imports_preserve_deferral_and_taint():
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
        "import": ("lazy import iast_lazy_target as target", "target.transform"),
        "from": ("lazy from iast_lazy_target import transform", "transform"),
        "modules_import": (
            '__lazy_modules__ = ["iast_lazy_target"]\nimport iast_lazy_target as target',
            "target.transform",
        ),
        "modules_from": (
            '__lazy_modules__ = ["iast_lazy_target"]\nfrom iast_lazy_target import transform',
            "transform",
        ),
    }
    statement, transform = imports[os.environ["_DD_TEST_LAZY_IMPORT"]]

    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "iast_lazy_target.py").write_text('def transform(value):\n    return "target:" + value\n')
        (root / "iast_lazy_importer.py").write_text(
            f'{statement}\n\ndef run(value):\n    return "caller:" + {transform}(value)\n'
        )
        sys.path.insert(0, directory)
        importer = importlib.import_module("iast_lazy_importer")
        assert "iast_lazy_target" not in sys.modules

        assert _iast_start_request() is not None
        try:
            value = taint_pyobject("untrusted", "parameter", "untrusted", OriginType.PARAMETER)
            for _ in range(2):
                result = importer.run(value)
                assert result == "caller:target:untrusted"
                assert "iast_lazy_target" in sys.modules
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
