import json
import os
import subprocess

import bm


class Startup(bm.Scenario):
    import_ddtrace: bool
    import_ddtrace_auto: bool
    ddtrace_run: bool
    import_flask: bool
    import_django: bool
    send_span: bool
    env: str
    flask_requests: int = 0

    # Not helpful for subprocess benchmarks
    cprofile_loops: int = 0

    def run(self):
        env = os.environ.copy()
        if self.env:
            parsed_env: dict[str, str] = json.loads(self.env)
            env.update(parsed_env)

        commands: list[str] = []
        if self.import_ddtrace:
            commands.append("import ddtrace")
        if self.import_ddtrace_auto:
            commands.append("import ddtrace.auto")

        # Import frameworks after instrumentation.
        if self.import_flask:
            commands.append("import flask")
        if self.import_django:
            # `import django` doesn't really do anything, `django.core.management` is what `manage.py` uses
            commands.append("import django.core.management")

        if self.flask_requests:
            commands.extend(
                [
                    "app = flask.Flask(__name__)",
                    "app.add_url_rule('/', 'index', lambda: 'ok')",
                    "client = app.test_client()",
                    f"responses = [client.get('/') for _ in range({self.flask_requests})]",
                    "assert all(response.status_code == 200 for response in responses)",
                ]
            )

        if self.send_span:
            commands.append("from ddtrace.trace import tracer")
            commands.append("tracer.trace('test-x', service='bench-test').finish()")

        args = ["python", "-c"] + [";".join(commands)]

        if self.ddtrace_run:
            args = ["ddtrace-run"] + args

        def _(loops: int):
            for _ in range(loops):
                subprocess.check_call(args=args, env=env)

        yield _
