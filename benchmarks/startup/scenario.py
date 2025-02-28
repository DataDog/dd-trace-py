import json
import os
import subprocess

import bm


class Startup(bm.Scenario):
    ddtrace_run: bool
    import_ddtrace: bool
    import_ddtrace_auto: bool
    import_flask: bool
    env: str

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

        # Always do this last, we want to import/patch ddtrace first
        # DEV: We don't need to create/start an app, we just want to see the impact on import time
        #      with and without patching. We have other benchmarks to test overhead of requests.
        if self.import_flask:
            commands.append("import flask")

        args = ["python", "-c"] + [";".join(commands)]
        if self.ddtrace_run:
            args = ["ddtrace-run"] + args

        def _(loops: int):
            for _ in range(loops):
                subprocess.check_call(args=args, env=env)

        yield _
