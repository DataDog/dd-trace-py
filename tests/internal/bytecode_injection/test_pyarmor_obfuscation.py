"""End-to-end validation against a real PyArmor-obfuscated module.

The check below runs via the ``@pytest.mark.subprocess`` marker rather than
in-process. The obfuscation runtime detection in
``ddtrace.internal.utils.obfuscation`` memoizes (for performance) whether a
PyArmor runtime module is loaded, based on the first call. Running in a fresh
subprocess guarantees the obfuscated module is imported before anything else
in this test session could have already triggered (and cached) that check.
"""

import pytest


pytest.importorskip("pyarmor")


@pytest.mark.subprocess(
    err=lambda err: "Cannot inject hook into 'add'" in err and "Cannot wrap 'add'" in err,
)
def test_pyarmor_obfuscated_code_is_detected_and_left_untouched():
    import subprocess
    import sys
    import tempfile

    fixture_dir = tempfile.mkdtemp()
    src_file = f"{fixture_dir}/pyarmor_fixture_module.py"
    with open(src_file, "w") as f:
        f.write("def add(a, b):\n    return a + b\n")

    dist_dir = f"{fixture_dir}/dist"
    subprocess.run(
        [sys.executable, "-m", "pyarmor.cli.__main__", "gen", "-O", dist_dir, src_file],
        check=True,
        capture_output=True,
        text=True,
    )

    sys.path.insert(0, dist_dir)
    import pyarmor_fixture_module

    from ddtrace.internal.bytecode_injection import inject_hook
    from ddtrace.internal.utils.obfuscation import is_obfuscated_code
    from ddtrace.internal.wrapping import wrap

    assert is_obfuscated_code(pyarmor_fixture_module.add.__code__), "obfuscated code was not detected"

    def normal():
        return 1

    assert not is_obfuscated_code(normal.__code__), "false positive on normal code"

    def hook(arg):
        pass

    original_code = pyarmor_fixture_module.add.__code__
    injected = inject_hook(pyarmor_fixture_module.add, hook, 1, None)
    assert injected.__code__ is original_code, "obfuscated code was rewritten by inject_hook"
    assert pyarmor_fixture_module.add(1, 2) == 3, "obfuscated function is no longer callable/correct"

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    wrapped = wrap(pyarmor_fixture_module.add, wrapper)
    assert wrapped is pyarmor_fixture_module.add, "obfuscated code was wrapped instead of left untouched"
