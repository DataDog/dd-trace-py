import os
import subprocess
import sys

import bm


class LangChainClassicStartup(bm.Scenario):
    iast_enabled: bool

    def run(self):
        env = os.environ.copy()
        env.update(
            DD_IAST_ENABLED=str(int(self.iast_enabled)),
            DD_REMOTE_CONFIGURATION_ENABLED="false",
            DD_INSTRUMENTATION_TELEMETRY_ENABLED="false",
            DD_TRACE_ENABLED="false",
        )
        command = [
            sys.executable,
            "-c",
            "import ddtrace.auto; import langchain_classic.agents",
        ]

        def benchmark(loops):
            for _ in range(loops):
                subprocess.run(command, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        yield benchmark
