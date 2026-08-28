import sys

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ bytecode")


def _exec_with_import_hooks(source: str):
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import inject_import_hooks
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import iter_import_events

    seen = []

    def hook(arg):
        seen.append(arg)

    code = compile(source, "<import-hook-test>", "exec")
    events = iter_import_events(code, "")
    exec(inject_import_hooks(code, hook, "<import-hook-test>", events), {})
    return seen


@pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="Accurate import hook injection is not supported on Python 3.15+"
)
def test_import_hook_injection_skips_runtime_false_import():
    seen = _exec_with_import_hooks("RUNTIME_FALSE = bool(0)\nif RUNTIME_FALSE:\n    import math\nimport json\n")

    assert (0, "<import-hook-test>", ("", ("json",))) in seen
    assert (0, "<import-hook-test>", ("", ("math",))) not in seen


@pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="Accurate import hook injection is not supported on Python 3.15+"
)
def test_import_hook_injection_tracks_function_local_import_only_when_called():
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import inject_import_hooks
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import iter_import_events

    seen = []

    def hook(arg):
        seen.append(arg)

    code = compile(
        "def import_in_function(cond):\n    if cond:\n        import decimal\n",
        "<import-hook-test>",
        "exec",
    )
    nested_code = next(const for const in code.co_consts if hasattr(const, "co_code"))
    nested_events = iter_import_events(nested_code, "")
    new_nested_code = inject_import_hooks(nested_code, hook, "<import-hook-test>", nested_events)
    consts = tuple(new_nested_code if const is nested_code else const for const in code.co_consts)
    namespace = {}
    exec(code.replace(co_consts=consts), namespace)

    namespace["import_in_function"](False)
    assert (0, "<import-hook-test>", ("", ("decimal",))) not in seen

    namespace["import_in_function"](True)
    assert (0, "<import-hook-test>", ("", ("decimal",))) in seen


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Accurate import hook injection is supported before Python 3.15")
def test_import_hook_injection_is_not_supported():
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import inject_import_hooks
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import iter_import_events

    code = compile("import json\n", "<import-hook-test>", "exec")

    with pytest.raises(NotImplementedError, match="Accurate import tracking is not supported on Python 3.15\\+"):
        inject_import_hooks(code, lambda _: None, "<import-hook-test>", iter_import_events(code, ""))


def test_import_event_extraction_groups_from_imports_by_line():
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import import_names_by_line
    from ddtrace.internal.coverage.import_instrumentation_py3_12 import iter_import_events

    code = compile("from tests.coverage.included_path import imported_in_function_lib\n", "<test>", "exec")
    names_by_line = import_names_by_line(iter_import_events(code, ""))

    assert names_by_line[1] == (
        "",
        (
            "tests.coverage.included_path",
            "tests.coverage.included_path.imported_in_function_lib",
        ),
    )
