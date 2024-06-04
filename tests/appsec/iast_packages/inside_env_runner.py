import ast
import importlib
import sys
from traceback import format_exc

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module

if hasattr(ast, 'unparse'):
    unparse = ast.unparse
else:
    from astunparse import unparse


def _iast_patched_module_and_patched_source(module_name):
    import importlib
    spec = importlib.util.find_spec(module_name)
    module_file_path = spec.origin

    # Read the source code from the file
    with open(module_file_path, 'r') as file:
        source_code = file.read()

    # Parse the source code into an AST
    parsed_ast = ast.parse(source_code)

    # Optionally, you can print or process the AST
    module = importlib.import_module(module_name)
    module_path, patched_module = astpatch_module(module)

    compiled_code = compile(patched_module, module_path, "exec")
    exec(compiled_code, module.__dict__)
    return module, patched_module


def try_unpatched(module_name):
    try:
        importlib.import_module(module_name)
        # TODO: check that the module is NOT patched
    except Exception as e:
        print(f"Unpatched import test failure: {module_name}:{format_exc()}")
        return 1
    return 0


def try_patched(module_name):
    try:
        module, patched_module = _iast_patched_module_and_patched_source(module_name)
        assert module, "Module is None after patching"
        assert patched_module, "Patched source is None after patching"
        new_code = unparse(patched_module)
        assert (
            "import ddtrace.appsec._iast.taint_sinks as ddtrace_taint_sinks"
            "\nimport ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects\n"
        ) in new_code, "Patched imports not found"

        assert "ddtrace_aspects." in new_code, "Patched aspects not found"
    except Exception:
        print(f"Patched import test failure: {module_name}: {format_exc()}")
        return 1
    return 0


if __name__ == "__main__":
    mode = sys.argv[1]
    import_module = sys.argv[2]

    if mode == "unpatched":
        sys.exit(try_unpatched(import_module))
    elif mode == "patched":
        sys.exit(try_patched(import_module))

    print("Use: [python from pyenv] inside_env_runner.py patched|unpatched module_name")
    sys.exit(1)
