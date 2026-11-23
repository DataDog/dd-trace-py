import sys

import pytest


# DEV: kept apart from test_module.py, whose type check follows an import of a deliberately broken test fixture.
@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="zipimporter has no exec_module before Python 3.10, so get_code is not wrapped"
)
@pytest.mark.subprocess
def test_module_watchdog_transform_sees_each_module_of_a_shared_loader():
    # Modules imported from the same zip file share one zipimporter, whose get_code is wrapped to run the
    # transformers: each import must be transformed with its own module, not the first one imported.
    import os
    import sys
    import tempfile
    import zipfile

    from ddtrace.internal.module import ModuleWatchdog

    seen = []

    class Recorder(ModuleWatchdog):
        def transform(self, code, module):
            if module.__name__.startswith("dd_shared_loader_"):
                seen.append(module.__name__)
            return code

    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, "modules.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("dd_shared_loader_a.py", "value = 'a'\n")
            zf.writestr("dd_shared_loader_b.py", "value = 'b'\n")
        sys.path.insert(0, archive)

        Recorder.install()
        try:
            import dd_shared_loader_a
            import dd_shared_loader_b

            assert (dd_shared_loader_a.value, dd_shared_loader_b.value) == ("a", "b")
        finally:
            Recorder.uninstall()
            sys.path.remove(archive)

    assert seen == ["dd_shared_loader_a", "dd_shared_loader_b"], seen
