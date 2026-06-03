import bm
from bm.iast_utils import IAST_ENV
from bm.iast_utils import _with_iast_context
from bm.iast_utils import _without_iast_context
from bm.iast_utils import asm_config
from bm.utils import override_env

from ddtrace.appsec._iast import enable_iast_propagation


with override_env(IAST_ENV):
    asm_config._iast_enabled = True
    asm_config._iast_propagation_enabled = True
    enable_iast_propagation()
    import functions  # noqa: E402


class IASTAspectsOsPath(bm.Scenario):
    iast_enabled: bool
    function_name: str
    internal_loop: int

    def run(self):
        def aspect_loop(loops):
            for _ in range(loops):
                for _ in range(self.internal_loop):
                    if self.iast_enabled:
                        # Taint the parameter for each iteration
                        _ = getattr(functions, self.function_name)()
                    else:
                        _ = getattr(functions, self.function_name)()

        context = _with_iast_context if self.iast_enabled else _without_iast_context
        with context():
            yield aspect_loop
