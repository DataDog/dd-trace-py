import os
import subprocess
import sys


def run_uwsgi(cmd):
    def _run(*args):
        env = os.environ.copy()
        python_libdir = os.path.join(sys.base_prefix, "lib")
        if "LD_LIBRARY_PATH" in env:
            # Prepend Python lib directory to existing LD_LIBRARY_PATH
            env["LD_LIBRARY_PATH"] = f"{python_libdir}:{env['LD_LIBRARY_PATH']}"
        else:
            env["LD_LIBRARY_PATH"] = python_libdir
        return subprocess.Popen(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)

    return _run
