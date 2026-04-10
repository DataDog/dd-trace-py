import subprocess

from ddtrace.internal.settings import env


def run_uwsgi(cmd):
    def _run(*args):
        subenv = env.copy()
        return subprocess.Popen(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=subenv)

    return _run
