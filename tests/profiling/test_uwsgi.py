# -*- encoding: utf-8 -*-
import os
import re
import signal
import sys
import tempfile
import time

import pytest

from tests.contrib.uwsgi import run_uwsgi

from . import utils


# uwsgi does not support Python 3.10 yet
# uwsgi is not available on Windows
if sys.version_info[:2] >= (3, 10) or sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")


@pytest.fixture
def uwsgi(monkeypatch):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = tempfile.mktemp()
    cmd = ["uwsgi", "--need-app", "--die-on-term", "--socket", socket_name, "--wsgi-file", uwsgi_app]

    try:
        yield run_uwsgi(cmd)
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi):
    proc = uwsgi()
    stdout, _ = proc.communicate()
    assert proc.wait() != 0
    assert b"ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true" in stdout


def test_uwsgi_threads_enabled(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads")
    worker_pids = _get_worker_pids(proc.stdout, 1)
    # Give some time to the process to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 30
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))


def test_uwsgi_threads_processes_no_master(uwsgi, monkeypatch):
    proc = uwsgi("--enable-threads", "--processes", "2")
    stdout, _ = proc.communicate()
    assert (
        b"ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout, num_worker, num_app_started=1):
    worker_pids = []
    started = 0
    while True:
        line = stdout.readline()
        if line == b"":
            break
        elif b"WSGI app 0 (mountpoint='') ready" in line:
            started += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line.decode())
            if m:
                worker_pids.append(int(m.group(1)))

        if len(worker_pids) == num_worker and num_app_started == started:
            break

    return worker_pids


def test_uwsgi_threads_processes_master(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))


# This test fails with greenlet 2: the uwsgi.atexit function that is being called and run the profiler stop procedure is
# interrupted randomly in the middle and has no time to flush out the profile.
@pytest.mark.skipif(TESTING_GEVENT, reason="Test fails with greenlet 2")
def test_uwsgi_threads_processes_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))


# For whatever reason this crashes easily on Python 2.7 with a segfault, and hangs on Python before 3.7.
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(uwsgi_backtrace+0x35) [0x561586ca5a85]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(uwsgi_segfault+0x23) [0x561586ca5e33]
# /lib/x86_64-linux-gnu/libc.so.6(+0x33060) [0x7f8325888060]
# /root/project/.tox/py27-profile-minreqs-gevent/lib/python2.7/site-packages/
#         google/protobuf/pyext/_message.so(+0x8d450) [0x7f831ed20450]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(+0x14ddb7) [0x561586d36db7]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(PyDict_SetItem+0x67) [0x561586d38327]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(_PyModule_Clear+0x14e) [0x561586d3d21e]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(PyImport_Cleanup+0x380) [0x561586daf440]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(+0x1db008) [0x561586dc4008]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(uwsgi_plugins_atexit+0x81) [0x561586ca2b51]
# /lib/x86_64-linux-gnu/libc.so.6(+0x35940) [0x7f832588a940]
# /lib/x86_64-linux-gnu/libc.so.6(+0x3599a) [0x7f832588a99a]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(+0x7172f) [0x561586c5a72f]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(end_me+0x25) [0x561586ca2b95]
# /lib/x86_64-linux-gnu/libpthread.so.0(+0x110e0) [0x7f83270a50e0]
# /lib/x86_64-linux-gnu/libc.so.6(epoll_wait+0x33) [0x7f832593e303]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(event_queue_wait+0x25) [0x561586c98d55]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(wsgi_req_accept+0x13a) [0x561586c57eba]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(simple_loop_run+0xb6) [0x561586ca1916]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(simple_loop+0x10) [0x561586ca1740]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(uwsgi_ignition+0x1b3) [0x561586ca60a3]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(uwsgi_worker_run+0x28d) [0x561586caaa6d]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(+0xc206c) [0x561586cab06c]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(+0x6e24e) [0x561586c5724e]
# /lib/x86_64-linux-gnu/libc.so.6(__libc_start_main+0xf1) [0x7f83258752e1]
# /root/project/.tox/py27-profile-minreqs-gevent/bin/uwsgi(_start+0x2a) [0x561586c5727a]
@pytest.mark.skipif(
    not (sys.version_info[0] >= 3 and sys.version_info[1] >= 7), reason="this test crashes on old Python versions"
)
# This test fails with greenlet 2: the uwsgi.atexit function that is being called and run the profiler stop procedure is
# interrupted randomly in the middle and has no time to flush out the profile.
@pytest.mark.skipif(TESTING_GEVENT, reason="Test fails with greenlet 2")
def test_uwsgi_threads_processes_no_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    # The processes are started without a master/parent so killing one does not kill the other:
    # Kill them all and wait until they die.
    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
    # The first worker is our child, we can wait for it "normally"
    os.waitpid(worker_pids[0], 0)
    # The other ones are grandchildren, we can't wait for it with `waitpid`
    for pid in worker_pids[1:]:
        # Wait for the uwsgi workers to all die
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                break
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))
