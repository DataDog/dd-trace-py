import bm
import utils


class DjangoSimple(bm.Scenario):
    tracer_enabled: bool
    profiler_enabled: bool
    appsec_enabled: bool
    iast_enabled: bool

    def run(self):
        with utils.server(self) as get_response:

            def _(loops):
                for _ in range(loops):
                    get_response()

            yield _
