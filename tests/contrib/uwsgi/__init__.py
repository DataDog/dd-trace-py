import os
import subprocess
import sys


def run_uwsgi(cmd):
    def _run(*args):
        env = os.environ.copy()
        if sys.version_info[0] == 2:
            # On Python2, it's impossible to import uwsgidecorators without this hack
            env["PYTHONPATH"] = os.path.join(
                os.environ.get("VIRTUAL_ENV", ""),
                "lib",
                "python%s.%s" % (sys.version_info[0], sys.version_info[1]),
                "site-packages",
            )

        return subprocess.Popen(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

    return _run
