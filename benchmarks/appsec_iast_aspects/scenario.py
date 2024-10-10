# import base64
import ast
import importlib
import types

import bm
from bm.utils import override_env

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._iast_request_context import end_iast_context
from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context import start_iast_context


# Copypasted here from tests.iast.aspects.conftest since the benchmarks can't access tests.*
def _iast_patched_module(module_name):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)
    return module_changed


def _start_iast_context_and_oce():
    oce.reconfigure()
    oce.acquire_request(None)
    start_iast_context()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()
    oce.release_request()


class IAST_Aspects(bm.Scenario):
    iast_enabled: bool
    mod_original_name: str
    function_name: str
    args: str

    def run(self):
        args = ast.literal_eval(self.args)
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _start_iast_context_and_oce()

        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    with override_env({"DD_IAST_ENABLED": "True"}):
                        module_patched = _iast_patched_module(self.mod_original_name)

                    getattr(module_patched, self.function_name)(*args)
                else:
                    module_unpatched = importlib.import_module(self.mod_original_name)
                    getattr(module_unpatched, self.function_name)(*args)

        yield _
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _end_iast_context_and_oce()
