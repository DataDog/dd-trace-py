import os
import subprocess
import sys
from typing import Dict
from typing import NamedTuple

import pytest
import tenacity

from ddtrace.internal.compat import stringify
from tests.webclient import Client


GunicornServerSettings = NamedTuple(
    "GunicornServerSettings",
    [
        ("env", Dict[str, str]),
        ("directory", str),
        ("app_path", str),
        ("num_workers", str),
        ("worker_class", str),
        ("bind", str),
        ("use_ddtracerun", bool),
        ("post_worker_init", str),
    ],
)


_post_worker_init_ddtrace = """
def post_worker_init(worker):
    import ddtrace.bootstrap.sitecustomize
"""


def _gunicorn_settings_factory(
    env=os.environ.copy(),  # type: Dict[str, str]
    directory=os.getcwd(),  # type: str
    app_path="tests.contrib.gunicorn.simple_app:app",  # type: str
    num_workers="4",  # type: str
    worker_class="sync",  # type: str
    bind="0.0.0.0:8080",  # type: str
    use_ddtracerun=True,  # type: bool
    post_worker_init="",  # type: str
):
    # type: (...) -> GunicornServerSettings
    """Factory for creating gunicorn settings with simple defaults if settings are not defined."""
    return GunicornServerSettings(
        env=env,
        directory=directory,
        app_path=app_path,
        num_workers=num_workers,
        worker_class=worker_class,
        bind=bind,
        use_ddtracerun=use_ddtracerun,
        post_worker_init=post_worker_init,
    )


@pytest.fixture
def gunicorn_server_settings():
    yield _gunicorn_settings_factory()


@pytest.fixture
def gunicorn_server(gunicorn_server_settings, tmp_path):
    cfg_file = tmp_path / "gunicorn.conf.py"
    cfg = """
{post_worker_init}

workers = {num_workers}
worker_class = "{worker_class}"
bind = "{bind}"
""".format(
        post_worker_init=gunicorn_server_settings.post_worker_init,
        bind=gunicorn_server_settings.bind,
        num_workers=gunicorn_server_settings.num_workers,
        worker_class=gunicorn_server_settings.worker_class,
    )
    cfg_file.write_text(stringify(cfg))
    cmd = []
    if gunicorn_server_settings.use_ddtracerun:
        cmd = ["ddtrace-run"]
    cmd += ["gunicorn", "--config", str(cfg_file), str(gunicorn_server_settings.app_path)]
    print("Running %r with configuration file %s" % (" ".join(cmd), cfg))
    p = subprocess.Popen(
        cmd,
        env=gunicorn_server_settings.env,
        cwd=gunicorn_server_settings.directory,
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://%s" % gunicorn_server_settings.bind)
        # Wait for the server to start up
        try:
            client.wait(max_tries=20, delay=0.5)
        except tenacity.RetryError:
            # if proc.returncode is not None:
            # process failed
            raise TimeoutError("Server failed to start, see stdout and stderr logs")

        yield client

        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass
    finally:
        p.terminate()
        p.wait()


def test_basic(gunicorn_server):
    r = gunicorn_server.get("/")
    assert r.status_code == 200
    assert r.content == b"Hello, World!\n"


@pytest.mark.snapshot(ignores=["meta.result_class"])
@pytest.mark.parametrize(
    "gunicorn_server_settings", [_gunicorn_settings_factory(app_path="tests.contrib.gunicorn.wsgi_mw_app:app")]
)
def test_traced_basic(gunicorn_server_settings, gunicorn_server):
    # meta.result_class is listiterator vs list_iterator in PY2 vs PY3.
    # Ignore this field to avoid having to create mostly duplicate snapshots in Python 2 and 3.
    r = gunicorn_server.get("/")
    assert r.status_code == 200
    assert r.content == b"Hello, World!\n"
