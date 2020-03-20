import os
import subprocess
import shlex

current_dir = os.path.dirname(__file__)

def test_uwsgi_threads_enabled():
    command = "uwsgi --http :9090 --wsgi-file {} --enable-threads --log-master".format(
        os.path.join(current_dir, "uwsgi_threads.py")
    )
    out = subprocess.check_output(shlex.split(command))
    assert b"threads support enabled" in out


def test_uwsgi_threads_not_enabled():
    command = "uwsgi --http :9090 --wsgi-file {} --log-master".format(
        os.path.join(current_dir, "uwsgi_threads.py")
    )
    out = subprocess.check_output(shlex.split(command))
    assert b"*** Python threads support is disabled" in out

def test_uwsgi_threads_not_set_in_tracer():
    command = "uwsgi --http :9090 --wsgi-file {} --log-master".format(
        os.path.join(current_dir, "uwsgi_threads.py")
    )
    out = subprocess.check_output(shlex.split(command))
    assert b"--enable-threads not set" in out

def test_uwsgi_threads_not_true_in_tracer():
    command = "uwsgi --http :9090 --wsgi-file {} --ini {} --log-master".format(
        os.path.join(current_dir, "uwsgi_threads.py"),
        os.path.join(current_dir, "uwsgi.ini")
    )
    out = subprocess.check_output(shlex.split(command))
    assert b"enable-threads=true is required" in out
