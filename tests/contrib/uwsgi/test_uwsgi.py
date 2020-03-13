import os
import subprocess
import shlex


def test_uwsgi_threads_enabled():
    command = "uwsgi --http :9090 --wsgi-file {} --log-master".format(
        os.path.join(os.path.dirname(__file__), "uwsgi_threads.py")
    )
    out = subprocess.check_output(shlex.split(command))
    assert b"Threads enabled" in out
