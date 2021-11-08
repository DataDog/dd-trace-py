import bm
import utils


class FlaskSimple(bm.Scenario):
    tracer_enabled = bm.bool()
    profiler_enabled = bm.bool()

    def run(self):
        with utils.server(self) as get_response:

            def _(loops):
                for _ in range(loops):
                    get_response()

            yield _
