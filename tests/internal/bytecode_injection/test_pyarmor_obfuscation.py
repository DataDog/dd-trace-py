"""End-to-end validation against a real PyArmor-obfuscated module.

The checks below run via the ``@pytest.mark.subprocess`` marker rather than
in-process, so that generating and importing a PyArmor-obfuscated fixture
module in one test cannot leave state (e.g. the obfuscation runtime detection
cache in ``ddtrace.internal.utils.obfuscation``) that affects another.
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


@pytest.mark.subprocess(
    err=lambda err: "Cannot wrap 'add'" in err,
)
def test_pyarmor_obfuscated_code_is_detected_when_runtime_loads_after_first_check():
    """A negative obfuscation-runtime check must not be cached: the PyArmor
    runtime can be imported lazily, after other code has already been
    checked (and found not obfuscated).
    """
    import subprocess
    import sys
    import tempfile

    from ddtrace.internal.utils.obfuscation import is_obfuscated_code

    def normal():
        return 1

    # Trigger (and, prior to the fix, permanently cache) a negative check
    # before the PyArmor runtime has ever been imported.
    assert not is_obfuscated_code(normal.__code__)

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

    from ddtrace.internal.wrapping import wrap

    assert is_obfuscated_code(pyarmor_fixture_module.add.__code__), "obfuscated code was not detected after late load"

    def wrapper(f, args, kwargs):
        return f(*args, **kwargs)

    wrapped = wrap(pyarmor_fixture_module.add, wrapper)
    assert wrapped is pyarmor_fixture_module.add, "obfuscated code was wrapped instead of left untouched"
