import os
import subprocess
import shlex

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi_threads.py")
uwsgi_ini = os.path.join(os.path.dirname(__file__), "uwsgi.ini")


def output(command):
    return subprocess.check_output(shlex.split(command))


def test_uwsgi_threads_enabled():
    cmd = "uwsgi --http :9090 --wsgi-file {} --enable-threads --log-master".format(uwsgi_app)
    assert b"threads support enabled" in output(cmd)


def test_uwsgi_threads_not_enabled():
    cmd = "uwsgi --http :9090 --wsgi-file {} --log-master".format(uwsgi_app)
    assert b"*** Python threads support is disabled" in output(cmd)


def test_uwsgi_threads_not_set_in_tracer():
    cmd = "uwsgi --http :9090 --wsgi-file {} --log-master".format(uwsgi_app)
    assert b"--enable-threads not set" in output(cmd)


def test_uwsgi_threads_not_true_in_tracer():
    cmd = "uwsgi --http :9090 --wsgi-file {} --ini {} --log-master".format(
        uwsgi_app, uwsgi_ini)
    assert b"enable-threads=true is required" in output(cmd)
