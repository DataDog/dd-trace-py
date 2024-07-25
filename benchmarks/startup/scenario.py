from dataclasses import field
import os
import subprocess

import bm


class Startup(bm.Scenario):
    ddtrace_run: bool
    import_ddtrace: bool
    import_ddtrace_auto: bool
    env: dict = field(default_factory=dict)

    def run(self):
        env = os.environ.copy()
        env.update(self.env)

        args = ["python", "-c", ""]
        if self.import_ddtrace:
            args = ["python", "-c", "import ddtrace"]
        elif self.import_ddtrace_auto:
            args = ["python", "-c", "import ddtrace.auto"]
        elif self.ddtrace_run:
            args = ["ddtrace-run", "python", "-c", ""]

        def _(loops):
            for _ in range(loops):
                subprocess.check_call(args, env=env)

        yield _
