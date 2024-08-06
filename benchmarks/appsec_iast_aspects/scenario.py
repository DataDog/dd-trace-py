# import base64
import importlib
import types

import bm
from bm.utils import override_env

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


# Copypasted here from tests.iast.aspects.conftest since the benchmarks can't access tests.*
def _iast_patched_module(module_name):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)
    return module_changed


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
                        module_patched = _iast_patched_module(self.mod_original_name)

                    getattr(module_patched, self.function_name)(*self.args)
                else:
                    module_unpatched = importlib.import_module(self.mod_original_name)
                    getattr(module_unpatched, self.function_name)(*self.args)

        yield _
