import sys

import pytest


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ bytecode")

# Accurate import-hook injection relies on INJECTION_ASSEMBLY (bytecode rewriting), which is only
# exposed by bytecode_injection on Python < 3.15. On 3.15+ that module switched to sys.monitoring
# and accurate imports are disabled (see instrumentation_py3_12._USE_ACCURATE_IMPORTS), so the
# inject_import_hooks/iter_import_events paths are unsupported there.
accurate_imports_only = pytest.mark.skipif(
    sys.version_info >= (3, 15), reason="Accurate import injection is not supported on Python 3.15+"
)


def test_coverage_import_chain_imports_on_all_supported_versions():
    # Regression test: on Python 3.15+, ddtrace.internal.bytecode_injection no longer exposes
    # INJECTION_ASSEMBLY (it switched to a sys.monitoring-based implementation). The coverage
    # import-instrumentation module imported it unconditionally, so merely loading the coverage
    # backend raised ImportError and broke all coverage collection on 3.15. The module must import
    # regardless of version.
    import ddtrace.internal.coverage.import_instrumentation_py3_12 as import_instr
    from ddtrace.internal.coverage.instrumentation import instrument_all_lines  # noqa: F401

    if sys.version_info < (3, 15):
        assert import_instr.INJECTION_ASSEMBLY is not None
    else:
        assert import_instr.INJECTION_ASSEMBLY is None


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


@accurate_imports_only
def test_import_hook_injection_skips_runtime_false_import():
    seen = _exec_with_import_hooks("RUNTIME_FALSE = bool(0)\nif RUNTIME_FALSE:\n    import math\nimport json\n")

    assert (0, "<import-hook-test>", ("", ("json",))) in seen
    assert (0, "<import-hook-test>", ("", ("math",))) not in seen


@accurate_imports_only
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


@accurate_imports_only
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
