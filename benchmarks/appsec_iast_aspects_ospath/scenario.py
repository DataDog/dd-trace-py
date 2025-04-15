import bm
from bm.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    # from ddtrace.appsec._iast import oce
    try:
        # 2.15+
        from ddtrace.appsec._iast._iast_request_context_base import end_iast_context
        from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
        from ddtrace.appsec._iast._iast_request_context_base import start_iast_context
    except ImportError:
        # Pre 2.15
        from ddtrace.appsec._iast._taint_tracking._context import create_context as start_iast_context
        from ddtrace.appsec._iast._taint_tracking._context import reset_context as end_iast_context

        set_iast_request_enabled = lambda x: None  # noqa: E731


def _start_iast_context_and_oce():
    # oce.reconfigure()
    # oce.acquire_request(None)
    start_iast_context()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()
    # oce.release_request()


with override_env({"DD_IAST_ENABLED": "True"}):
    import functions


class IAST_Aspects(bm.Scenario):
    iast_enabled: bool
    function_name: str

    def run(self):
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _start_iast_context_and_oce()

        def _(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    with override_env({"DD_IAST_ENABLED": "True"}):
                        getattr(functions, self.function_name)()

                else:
                    getattr(functions, self.function_name)()

        yield _
        if self.iast_enabled:
            with override_env({"DD_IAST_ENABLED": "True"}):
                _end_iast_context_and_oce()
