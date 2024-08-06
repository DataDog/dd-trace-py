# import base64
import importlib
import types

import bm
from bm.utils import override_env

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


def _iast_patched_module_and_patched_source(module_name, new_module_object=False):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name) if new_module_object else module
    exec(compiled_code, module_changed.__dict__)
    return module_changed, patched_source


def _iast_patched_module(module_name, new_module_object=False):
    module, _ = _iast_patched_module_and_patched_source(module_name, new_module_object)
    return module


class IAST_Aspects(bm.Scenario):
    iast_enabled: bool
    mod_original_name: str
    function_name: str
    args: list

    def run(self):
        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    with override_env({"DD_IAST_ENABLED": "True"}):
                        from tests.appsec.iast.aspects.conftest import _iast_patched_module

                        module_patched = _iast_patched_module(self.mod_original_name)

                    getattr(module_patched, self.function_name)(*self.args)
                else:
                    module_unpatched = importlib.import_module(self.mod_original_name)
                    getattr(module_unpatched, self.function_name)(*self.args)

        yield _
