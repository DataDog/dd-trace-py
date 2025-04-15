import hashlib
import os
import random
import sqlite3

from flask import Flask
from flask import Response
from flask import render_template_string
from flask import request

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

    con = sqlite3.connect(":memory:", check_same_thread=False)
    cur = con.cursor()

    @app.route("/")
    def index():
        return make_index()

    @app.route("/post-view", methods=["POST"])
    def post_view():
        data = request.data
        return data, 200

    @app.route("/sqli", methods=["POST"])
    def sqli():
        sql = "SELECT 1 FROM sqlite_master WHERE name = '" + request.form["username"] + "'"
        cur.execute(sql)
        return Response("OK")

    @app.route("/sqli_with_errortracking", methods=["POST"])
    def sqli_with_errortracking():
        try:
            sql = "SELECT 1 FROM sqlite_master WHERE name = '" + request.form["username"] + "'"
            cur.execute(sql)
            raise ValueError("there has been a sql error")
        except ValueError:
            return Response("OK")

    return app


class FlaskScenarioMixin:
    def setup(self):
        # Setup the environment and enable Datadog features
        os.environ.update(
            {
                "DD_APPSEC_ENABLED": str(self.appsec_enabled),
                "DD_IAST_ENABLED": str(self.iast_enabled),
                "DD_TELEMETRY_METRICS_ENABLED": str(self.telemetry_metrics_enabled),
            }
        )

        if self.errortracking_enabled:
            os.environ.update({"DD_ERROR_TRACKING_HANDLED_ERRORS_ENABLED": self.errortracking_enabled})

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

    def create_app(self):
        self.setup()
        return create_app()
