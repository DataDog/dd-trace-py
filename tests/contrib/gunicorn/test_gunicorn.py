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

from ddtrace.internal.compat import stringify
from tests.webclient import Client


SERVICE_INTERVAL = 1
# this is the most direct manifestation i can find of a bug caused by misconfigured gunicorn+ddtrace
MOST_DIRECT_KNOWN_GUNICORN_RELATED_PROFILER_ERROR_SIGNAL = b"RuntimeError: the memalloc module is already started"


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
        ("import_sitecustomize_in_postworkerinit", bool),
        ("start_service_in_hook_named", str),
    ],
)


IMPORT_SITECUSTOMIZE = "import ddtrace.bootstrap.sitecustomize"
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "post_fork.py"), "r") as f:
    code = f.readlines()
START_SERVICE = "    " + "    ".join(code)


def assert_no_profiler_error(server_process):
    assert MOST_DIRECT_KNOWN_GUNICORN_RELATED_PROFILER_ERROR_SIGNAL not in server_process.stderr.read()


def parse_payload(data):
    decoded = data
    if sys.version_info[1] == 5:
        decoded = data.decode("utf-8")
    return json.loads(decoded)


def assert_remoteconfig_started_successfully(response, check_patch=True):
    # ddtrace and gunicorn don't play nicely under python 3.5 or 3.11
    if sys.version_info[1] in (5, 11):
        return
    assert response.status_code == 200
    payload = parse_payload(response.content)
    assert payload["remoteconfig"]["worker_alive"] is True
    if check_patch:
        assert payload["remoteconfig"]["enabled_after_gevent_monkeypatch"] is True


def _gunicorn_settings_factory(
    env=None,  # type: Dict[str, str]
    directory=os.getcwd(),  # type: str
    app_path="tests.contrib.gunicorn.wsgi_mw_app:app",  # type: str
    num_workers="4",  # type: str
    worker_class="sync",  # type: str
    bind="0.0.0.0:8080",  # type: str
    use_ddtracerun=True,  # type: bool
    import_sitecustomize_in_postworkerinit=False,  # type: bool
    patch_gevent=None,  # type: Optional[bool]
    import_sitecustomize_in_app=None,  # type: Optional[bool]
    start_service_in_hook_named="post_fork",  # type: str
    enable_module_cloning=False,  # type: bool
):
    # type: (...) -> GunicornServerSettings
    """Factory for creating gunicorn settings with simple defaults if settings are not defined."""
    if env is None:
        env = os.environ.copy()
    if patch_gevent is not None:
        env["DD_GEVENT_PATCH_ALL"] = str(patch_gevent)
    if import_sitecustomize_in_app is not None:
        env["_DD_TEST_IMPORT_SITECUSTOMIZE"] = str(import_sitecustomize_in_app)
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "1" if enable_module_cloning else "0"
    env["DD_REMOTECONFIG_POLL_SECONDS"] = str(SERVICE_INTERVAL)
    env["DD_PROFILING_UPLOAD_INTERVAL"] = str(SERVICE_INTERVAL)
    return GunicornServerSettings(
        env=env,
        directory=directory,
        app_path=app_path,
        num_workers=num_workers,
        worker_class=worker_class,
        bind=bind,
        use_ddtracerun=use_ddtracerun,
        import_sitecustomize_in_postworkerinit=import_sitecustomize_in_postworkerinit,
        start_service_in_hook_named=start_service_in_hook_named,
    )


def build_config_file(gunicorn_server_settings):
    post_fork = START_SERVICE if gunicorn_server_settings.start_service_in_hook_named != "post_worker_init" else ""
    post_worker_init = "    {sitecustomize}\n{service_start}".format(
        sitecustomize=IMPORT_SITECUSTOMIZE if gunicorn_server_settings.import_sitecustomize_in_postworkerinit else "",
        service_start=START_SERVICE
        if gunicorn_server_settings.start_service_in_hook_named == "post_worker_init"
        else "",
    )
    cfg = """
def post_fork(server, worker):
    pass
{post_fork}

def post_worker_init(worker):
    pass
{post_worker_init}

workers = {num_workers}
worker_class = "{worker_class}"
bind = "{bind}"
""".format(
        post_fork=post_fork,
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
    cfg_file.write_text(stringify(cfg))
    cmd = []
    if gunicorn_server_settings.use_ddtracerun:
        cmd = ["ddtrace-run"]
    cmd += ["gunicorn", "--config", str(cfg_file), str(gunicorn_server_settings.app_path)]
    print("Running %r with configuration file %s" % (" ".join(cmd), cfg))

    server_process = subprocess.Popen(
        cmd,
        env=gunicorn_server_settings.env,
        cwd=gunicorn_server_settings.directory,
        stderr=subprocess.PIPE,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    try:
        client = Client("http://%s" % gunicorn_server_settings.bind)
        try:
            client.wait(max_tries=100, delay=0.1)
        except tenacity.RetryError:
            raise TimeoutError("Server failed to start, see stdout and stderr logs")
        # wait for services to wake up and decide whether to self-destruct due to PeriodicThread._is_proper_class
        time.sleep(SERVICE_INTERVAL)
        yield server_process, client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass
    finally:
        server_process.terminate()
        server_process.wait()


SETTINGS_GEVENT_DDTRACERUN_MODULE_CLONE = _gunicorn_settings_factory(
    worker_class="gevent", patch_gevent=False, enable_module_cloning=True
)
SETTINGS_GEVENT_DDTRACERUN_PATCH = _gunicorn_settings_factory(worker_class="gevent", patch_gevent=True)
SETTINGS_GEVENT_APPIMPORT_PATCH_POSTWORKERSERVICE = _gunicorn_settings_factory(
    worker_class="gevent",
    use_ddtracerun=False,
    import_sitecustomize_in_app=True,
    patch_gevent=True,
    start_service_in_hook_named="post_worker_init",
)
SETTINGS_GEVENT_POSTWORKERIMPORT_PATCH_POSTWORKERSERVICE = _gunicorn_settings_factory(
    worker_class="gevent",
    use_ddtracerun=False,
    import_sitecustomize_in_postworkerinit=True,
    patch_gevent=True,
    start_service_in_hook_named="post_worker_init",
)


@pytest.mark.skipif(sys.version_info > (3, 10), reason="Gunicorn is only supported up to 3.10")
@pytest.mark.parametrize(
    "gunicorn_server_settings",
    [
        SETTINGS_GEVENT_APPIMPORT_PATCH_POSTWORKERSERVICE,
        SETTINGS_GEVENT_POSTWORKERIMPORT_PATCH_POSTWORKERSERVICE,
        SETTINGS_GEVENT_DDTRACERUN_MODULE_CLONE,
    ],
)
def test_no_known_errors_occur(gunicorn_server_settings, tmp_path):
    with gunicorn_server(gunicorn_server_settings, tmp_path) as context:
        server_process, client = context
        r = client.get("/")
    assert_no_profiler_error(server_process)
    assert_remoteconfig_started_successfully(r, gunicorn_server_settings.env["DD_GEVENT_PATCH_ALL"] == "True")


@pytest.mark.parametrize(
    "gunicorn_server_settings",
    [
        SETTINGS_GEVENT_DDTRACERUN_PATCH,
    ],
)
def test_profiler_error_occurs_under_gevent_worker(gunicorn_server_settings, tmp_path):
    with gunicorn_server(gunicorn_server_settings, tmp_path) as context:
        server_process, client = context
        r = client.get("/")
    # this particular error does not manifest in 3.8 and older
    if sys.version_info >= (3, 9):
        assert MOST_DIRECT_KNOWN_GUNICORN_RELATED_PROFILER_ERROR_SIGNAL in server_process.stderr.read()
    assert_remoteconfig_started_successfully(r)
