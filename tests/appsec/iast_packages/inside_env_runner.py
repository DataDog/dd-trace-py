from ast import unparse
import importlib
import inspect
import pkgutil
import re
import sys
from traceback import format_exc
from types import ModuleType
from typing import Any

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


def _code_files(obj: Any) -> set[str]:
    if inspect.isclass(obj):
        candidates = [
            (klass.__qualname__ + ".", value)
            for klass in obj.__mro__
            if klass.__module__ == obj.__module__
            for value in vars(klass).values()
        ]
    else:
        candidates = [("", obj)]
    files = set()
    for owner_prefix, value in candidates:
        value = getattr(value, "__func__", value)
        if isinstance(value, property):
            value = value.fget
        try:
            value = inspect.unwrap(value)
        except ValueError:
            continue
        if not getattr(value, "__qualname__", "").startswith(owner_prefix):
            continue
        code = getattr(value, "__code__", None)
        if code is not None and not code.co_filename.startswith("<"):
            files.add(code.co_filename)
    return files


def _import_module(selector: str) -> ModuleType:
    """Import a module name, or the submodule that defines the attribute named by module:attribute.

    The attribute must be a class or function whose module is inside the selected package. When the
    attribute has Python code, at least one of its functions or methods must be in that module's file,
    so a re-export module is rejected. Only functions defined in the class body count, not helpers such
    as the ones dataclasses attach. A class without such methods must have its class statement in that
    module's source. Classes from compiled extensions are rejected, since IAST can't patch them.
    """
    module_name, _, attribute = selector.partition(":")
    if not attribute:
        return importlib.import_module(module_name)
    obj = pkgutil.resolve_name(selector)
    if not (inspect.isclass(obj) or inspect.isfunction(inspect.unwrap(obj))):
        raise ValueError(f"{selector} must name a class or function, not {type(obj).__name__}")
    defining_name = getattr(obj, "__module__", None)
    if not isinstance(defining_name, str) or not defining_name.startswith(module_name + "."):
        raise ValueError(
            f"{selector} must name a class or function defined in a submodule of {module_name}, "
            f"but its module is {defining_name!r}"
        )
    module = importlib.import_module(defining_name)
    code_files = _code_files(obj)
    if code_files and getattr(module, "__file__", None) not in code_files:
        raise ValueError(f"{selector} is only re-exported by {defining_name}; its code is in {sorted(code_files)}")
    if not code_files:
        try:
            source_file = inspect.getsourcefile(obj)
        except TypeError:
            source_file = None
        if source_file is None:
            raise ValueError(f"{selector} is defined in a compiled extension ({defining_name}), which IAST can't patch")
        try:
            source = inspect.getsource(obj)
        except OSError:
            raise ValueError(f"{selector} has no definition in {defining_name}") from None
        if not re.search(rf"^\s*(class|def) {re.escape(obj.__name__)}\b", source, re.MULTILINE):
            raise ValueError(f"{selector} has no definition in {defining_name}")
    print(f"Resolved {selector} to {defining_name}")
    return module


def _iast_patched_module_and_patched_source(module_name):
    module = _import_module(module_name)
    module_path, patched_module = astpatch_module(module)

    if not patched_module:
        assert False, "Module %s was not patched" % module_name

    compiled_code = compile(patched_module, module_path, "exec")
    exec(compiled_code, module.__dict__)
    return module, patched_module


def try_unpatched(module_name):
    try:
        _import_module(module_name)
        # TODO: check that the module is NOT patched
    except Exception:
        print(f"Unpatched import test failure: {module_name}:{format_exc()}")
        return 1
    return 0


def try_patched(module_name, expect_no_change=False):
    try:
        module, patched_module = _iast_patched_module_and_patched_source(module_name)
        if expect_no_change:
            assert module, "Module is not OK after patching"
            assert not patched_module, (
                "Patched source is None after patching: Expected to be the same as the original source"
            )
            return 0

        assert module, "Module is None after patching: Maybe not an error, but something fishy is going on"
        assert patched_module, (
            "Patched source is None after patching: Maybe not an error, but something fishy is going on"
        )
        new_code = unparse(patched_module)
        assert (
            "import ddtrace.appsec._iast.taint_sinks as _ddtrace_taint_sinks"
            "\nimport ddtrace.appsec._iast._taint_tracking.aspects as _ddtrace_aspects\n"
        ) in new_code, "Patched imports not found"

        assert "ddtrace_aspects." in new_code, "Patched aspects not found"
    except Exception:
        print(f"Patched import test failure: {module_name}: {format_exc()}")
        return 1
    return 0


if __name__ == "__main__":
    mode = sys.argv[1]
    import_module = sys.argv[2]
    if len(sys.argv) >= 4:
        expect_no_change = sys.argv[3]

    if mode == "unpatched":
        sys.exit(try_unpatched(import_module))
    elif mode == "patched":
        sys.exit(try_patched(import_module, expect_no_change=expect_no_change == "True"))

    print("Use: [python from pyenv] inside_env_runner.py patched|unpatched module_name|module:attribute True|False")
    sys.exit(1)
