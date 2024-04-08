import hashlib
import random
import os

from flask import Flask
from flask import render_template_string
from flask import request


import bm
import bm.utils as utils
from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogLineProbe


def make_index():
    rand_numbers = [random.random() for _ in range(20)]
    m = hashlib.md5()
    m.update(b"Insecure hash")
    rand_numbers.append(m.digest())
    return render_template_string(
        """
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Hello World!</title>
  </head>
  <body>
  <section class="section">
    <div class="container">
      <h1 class="title">
        Hello World
      </h1>
      <p class="subtitle">
        My first website
      </p>
      <ul>
        {% for i in rand_numbers %}
          <li>{{ i }}</li>
        {% endfor %}
      </ul>
    </div>
  </section>
  </body>
</html>
    """,
        rand_numbers=rand_numbers,
    )


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return make_index()

    @app.route("/post-view", methods=["POST"])
    def post_view():
        data = request.data
        return data, 200

    return app


class FlaskSimple(bm.Scenario):
    tracer_enabled = bm.var_bool()
    profiler_enabled = bm.var_bool()
    debugger_enabled = bm.var_bool()
    appsec_enabled = bm.var_bool()
    iast_enabled = bm.var_bool()
    post_request = bm.var_bool()
    telemetry_metrics_enabled = bm.var_bool()

    def run(self):
        # Setup the environment and enable Datadog features
        os.environ.update(
            {
                "DD_APPSEC_ENABLED": str(self.appsec_enabled),
                "DD_IAST_ENABLED": str(self.iast_enabled),
                "DD_TELEMETRY_METRICS_ENABLED": str(self.telemetry_metrics_enabled),
            }
        )
        if self.profiler_enabled:
            os.environ.update(
                {"DD_PROFILING_ENABLED": "1", "DD_PROFILING_API_TIMEOUT": "0.1", "DD_PROFILING_UPLOAD_INTERVAL": "10"}
            )
            if not self.tracer_enabled:
                import ddtrace.profiling.auto  # noqa:F401

        if self.tracer_enabled:
            import ddtrace.bootstrap.sitecustomize  # noqa:F401

        if self.debugger_enabled:
            from bm.di_utils import BMDebugger

            BMDebugger.enable()

            # Probes are added only if the BMDebugger is enabled.
            probe_id = "bm-test"
            BMDebugger.add_probes(
                LogLineProbe(
                    probe_id=probe_id,
                    version=0,
                    tags={},
                    source_file="scenario.py",
                    line=23,
                    template=probe_id,
                    segments=[LiteralTemplateSegment(probe_id)],
                    take_snapshot=True,
                    limits=DEFAULT_CAPTURE_LIMITS,
                    condition=None,
                    condition_error_rate=0.0,
                    rate=DEFAULT_SNAPSHOT_PROBE_RATE,
                ),
            )

        # Create the Flask app
        app = create_app()

        # Setup the request function
        if self.post_request:
            HEADERS = {
                "SERVER_PORT": "8000",
                "REMOTE_ADDR": "127.0.0.1",
                "CONTENT_TYPE": "application/json",
                "HTTP_HOST": "localhost:8000",
                "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
                "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
                "HTTP_SEC_FETCH_DEST": "document",
                "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
                "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
                "User-Agent": "dd-test-scanner-log",
            }

            def make_request(app):
                client = app.test_client()
                return client.post("/post-view", headers=HEADERS, data=utils.EXAMPLE_POST_DATA)
        else:

            def make_request(app):
                client = app.test_client()
                return client.get("/")

        # Scenario loop function
        def _(loops):
            for _ in range(loops):
                res = make_request(app)
                assert res.status_code == 200
                # We have to close the request (or read `res.data`) to get the `flask.request` span to finalize
                res.close()

        yield _
