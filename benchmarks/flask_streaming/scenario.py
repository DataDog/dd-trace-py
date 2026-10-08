import bm
from bm.flask_utils import FlaskScenarioMixin


class FlaskStreaming(bm.Scenario, FlaskScenarioMixin):
    # DEV: These mirror FlaskSimple so FlaskScenarioMixin.setup() can read them.
    tracer_enabled: bool
    profiler_enabled: bool
    debugger_enabled: bool
    appsec_enabled: bool
    iast_enabled: bool
    post_request: bool
    telemetry_metrics_enabled: bool
    errortracking_enabled: str
    resource_renaming_enabled: bool
    chunks: int

    def run(self):
        app = self.create_app()

        path = "/stream?chunks=%d" % self.chunks

        def make_request(app):
            client = app.test_client()
            return client.get(path)

        def _(loops):
            for _ in range(loops):
                res = make_request(app)
                assert res.status_code == 200
                # Consume the streamed body so the response wrapper is iterated
                # to exhaustion and the request span is finalized.
                res.get_data()
                res.close()

        yield _
