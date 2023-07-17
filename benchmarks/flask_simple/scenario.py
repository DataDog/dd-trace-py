import bm
import bm.flask_utils as flask_utils
from utils import _post_response


class FlaskSimple(bm.Scenario):
    tracer_enabled = bm.var_bool()
    profiler_enabled = bm.var_bool()
    appsec_enabled = bm.var_bool()
    iast_enabled = bm.var_bool()
    post_request = bm.var_bool()
    telemetry_metrics_enabled = bm.var_bool()

    def run(self):
        with flask_utils.server(self, custom_post_response=_post_response) as get_response:

            def _(loops):
                for _ in range(loops):
                    get_response()

            yield _
