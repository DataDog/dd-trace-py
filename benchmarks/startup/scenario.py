from dataclasses import dataclass
from dataclasses import field
import os
import subprocess

import bm


@dataclass
class StartupParent:
    name: str
    ddtrace_run: bool = field(default_factory=bm.var_bool)
    import_ddtrace: bool = field(default_factory=bm.var_bool)
    import_ddtrace_auto: bool = field(default_factory=bm.var_bool)
    env: dict = field(default_factory=dict)


class Startup(StartupParent, bm.Scenario):
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
