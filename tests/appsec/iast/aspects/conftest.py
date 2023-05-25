from types import ModuleType

from ddtrace.appsec.iast._ast.ast_patching import astpatch_module


# @pytest.fixture
def _iast_patched_module(module_name):
    module = __import__(module_name, fromlist=[None])
    module_path, patched_source = astpatch_module(module)

    compiled_code = compile(patched_source, module_path, "exec")
    exec(compiled_code, module.__dict__)
    return module
