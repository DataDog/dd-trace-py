from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time
from typing import Dict
from typing import NamedTuple
from typing import Optional  # noqa

import pytest
import tenacity

from ddtrace.internal import compat
from tests.webclient import Client


SERVICE_INTERVAL = 1


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
        ("import_auto_in_postworkerinit", bool),
    ],
)


IMPORT_AUTO = "import ddtrace.auto"


def parse_payload(data):
    decoded = data
    if sys.version_info[1] == 5:
        decoded = data.decode("utf-8")
    return json.loads(decoded)


def _gunicorn_settings_factory(
    env=None,  # type: Dict[str, str]
    directory=os.getcwd(),  # type: str
    app_path="tests.contrib.gunicorn.wsgi_mw_app:app",  # type: str
    num_workers="4",  # type: str
    worker_class="sync",  # type: str
    bind="0.0.0.0:8080",  # type: str
    use_ddtracerun=True,  # type: bool
    import_auto_in_postworkerinit=False,  # type: bool
    import_auto_in_app=None,  # type: Optional[bool]
    enable_module_cloning=False,  # type: bool
    debug_mode=False,  # type: bool
):
    # type: (...) -> GunicornServerSettings
    """Factory for creating gunicorn settings with simple defaults if settings are not defined."""
    if env is None:
        env = os.environ.copy()
    if import_auto_in_app is not None:
        env["_DD_TEST_IMPORT_AUTO"] = str(import_auto_in_app)
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "1" if enable_module_cloning else "0"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = str(True)
    env["DD_REMOTECONFIG_POLL_INTERVAL_SECONDS"] = str(SERVICE_INTERVAL)
    env["DD_PROFILING_UPLOAD_INTERVAL"] = str(SERVICE_INTERVAL)
    env["DD_TRACE_DEBUG"] = str(debug_mode)
    return GunicornServerSettings(
        env=env,
        directory=directory,
        app_path=app_path,
        num_workers=num_workers,
        worker_class=worker_class,
        bind=bind,
        use_ddtracerun=use_ddtracerun,
        import_auto_in_postworkerinit=import_auto_in_postworkerinit,
    )


def build_config_file(gunicorn_server_settings):
    post_worker_init = "    {}".format(
        IMPORT_AUTO if gunicorn_server_settings.import_auto_in_postworkerinit else "",
    )
    cfg = """
def post_worker_init(worker):
    pass
{post_worker_init}

workers = {num_workers}
worker_class = "{worker_class}"
bind = "{bind}"
""".format(
        post_worker_init=post_worker_init,
        bind=gunicorn_server_settings.bind,
        num_workers=gunicorn_server_settings.num_workers,
        worker_class=gunicorn_server_settings.worker_class,
    )
    return cfg


@contextmanager
def gunicorn_server(gunicorn_server_settings, tmp_path):
    cfg_file = tmp_path / "gunicorn.conf.py"
    cfg = build_config_file(gunicorn_server_settings)
    cfg_file.write_text(compat.stringify(cfg))
    cmd = []
    if gunicorn_server_settings.use_ddtracerun:
        cmd = ["ddtrace-run"]
    cmd += ["gunicorn", "--config", str(cfg_file), str(gunicorn_server_settings.app_path)]
    print("Running %r with configuration file %s" % (" ".join(cmd), cfg))
    gunicorn_server_settings.env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    server_process = subprocess.Popen(
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
        try:
            print("Waiting for server to start")
            client.wait(max_tries=100, delay=0.1)
            print("Server started")
        except tenacity.RetryError:
            raise TimeoutError("Server failed to start, see stdout and stderr logs")
        time.sleep(SERVICE_INTERVAL)
        yield server_process, client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass
    finally:
        server_process.terminate()
        server_process.wait()


SETTINGS_GEVENT_DDTRACERUN_MODULE_CLONE = _gunicorn_settings_factory(worker_class="gevent", enable_module_cloning=True)
SETTINGS_GEVENT_DDTRACERUN = _gunicorn_settings_factory(
    worker_class="gevent",
)
SETTINGS_GEVENT_APPIMPORT = _gunicorn_settings_factory(
    worker_class="gevent",
    use_ddtracerun=False,
    import_auto_in_app=True,
)
SETTINGS_GEVENT_POSTWORKERIMPORT = _gunicorn_settings_factory(
    worker_class="gevent",
    use_ddtracerun=False,
    import_auto_in_postworkerinit=True,
)
SETTINGS_GEVENT_DDTRACERUN_DEBUGMODE_MODULE_CLONE = _gunicorn_settings_factory(
    worker_class="gevent",
    debug_mode=True,
    enable_module_cloning=True,
)


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="Gunicorn is only supported up to 3.10")
@pytest.mark.parametrize(
    "gunicorn_server_settings",
    [
        SETTINGS_GEVENT_APPIMPORT,
        SETTINGS_GEVENT_POSTWORKERIMPORT,
        SETTINGS_GEVENT_DDTRACERUN,
        SETTINGS_GEVENT_DDTRACERUN_MODULE_CLONE,
        SETTINGS_GEVENT_DDTRACERUN_DEBUGMODE_MODULE_CLONE,
    ],
)
def test_no_known_errors_occur(gunicorn_server_settings, tmp_path):
    with gunicorn_server(gunicorn_server_settings, tmp_path) as context:
        _, client = context
        response = client.get("/")
    assert response.status_code == 200
    payload = parse_payload(response.content)
    assert payload["profiler"]["is_active"] is True
