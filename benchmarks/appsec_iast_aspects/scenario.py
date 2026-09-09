import bm
from bm.iast_utils import IAST_ENV
from bm.iast_utils import _with_iast_context
from bm.iast_utils import _without_iast_context
from bm.iast_utils import asm_config
from bm.utils import override_env

from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


with override_env(IAST_ENV):
    asm_config._iast_enabled = True
    asm_config._iast_propagation_enabled = True
    enable_iast_propagation()
    import functions  # noqa: E402


class IASTAspects(bm.Scenario):
    iast_enabled: bool
    function_name: str
    default_function_type: str
    internal_loop: int

    def run(self):
        # Create a tainted parameter to pass to functions
        tainted_param = "example_string %s"
        if self.default_function_type == "str":
            tainted_param = "example_string_str %s"
        elif self.default_function_type == "bytes":
            tainted_param = b"example_string_bytes %s"
        elif self.default_function_type == "bytearray":
            tainted_param = bytearray(b"example_string_bytearray %s")

        def aspect_loop(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    # Taint the parameter for each iteration
                    tainted = taint_pyobject(
                        tainted_param,
                        source_name="path2",
                        source_value=tainted_param,
                        source_origin=OriginType.PARAMETER,
                    )
                    for _ in range(self.internal_loop):
                        _ = getattr(functions, self.function_name)(tainted)
                    # DEBUG-NOTE: Uncomment to test locally that everything is working correctly
                    # from ddtrace.appsec._iast._taint_tracking import is_tainted
                    # assert is_tainted(result)
                else:
                    for _ in range(self.internal_loop):
                        _ = getattr(functions, self.function_name)(tainted_param)

        context = _with_iast_context if self.iast_enabled else _without_iast_context
        with context():
            yield aspect_loop
            if self.iast_enabled:
                num_tainted = _num_objects_tainted_in_request()
                context_size = debug_context_array_size()
                free_contexts = debug_context_array_free_slots_number()
                assert num_tainted > 0
                assert context_size == 2
                assert free_contexts > 0
