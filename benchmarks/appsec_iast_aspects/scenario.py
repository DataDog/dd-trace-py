import importlib

from benchmarks import bm
from benchmarks.bm.utils import override_env


class IAST_Aspects(bm.Scenario):
    iast_enabled: bool
    mod_original_name: str
    function_name: str
    args: list

    def run(self):
        # JJJ test
        self.iast_enabled = True
        self.mod_original_name = "tests.appsec.iast.fixtures.aspects.str_methods"
        self.function_name = "do_re_sub"
        self.args = ["foobar", "o", "a", 1]

        module_unpatched = importlib.import_module(self.mod_original_name)

        with override_env({"DD_IAST_ENABLED": "True"}):
            from tests.appsec.iast.aspects.conftest import _iast_patched_module

            module_patched = _iast_patched_module(self.mod_original_name)

        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    getattr(module_patched, self.function_name)(*self.args)
                else:
                    getattr(module_unpatched, self.function_name)(*self.args)

        yield _
