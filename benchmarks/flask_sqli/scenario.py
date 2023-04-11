import bm
import utils


class FlaskSQLi(bm.Scenario):
    tracer_enabled = bm.var_bool()
    profiler_enabled = bm.var_bool()
    appsec_enabled = bm.var_bool()
    iast_enabled = bm.var_bool()
    post_request = bm.var_bool()
    telemetry_metrics_enabled = bm.var_bool()

    def run(self):
        with utils.server(self) as get_response:

            def _(loops):
                for _ in range(500):
                    get_response()

            yield _
