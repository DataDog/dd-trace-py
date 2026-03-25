import os
import types

from ddtrace.commands import _dd_crashtracker_receiver
from ddtrace.internal.core import crashtracking


def test_get_active_site_packages_filters_sys_path(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    site_packages = tmp_path / "venv" / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True)
    dist_packages = tmp_path / "usr" / "lib" / "python3" / "dist-packages"
    dist_packages.mkdir(parents=True)

    monkeypatch.setattr(
        crashtracking.sys,
        "path",
        [
            "",
            str(project_dir),
            str(site_packages),
            str(dist_packages),
            str(site_packages),
        ],
    )

    assert crashtracking._get_active_site_packages() == [
        os.path.realpath(site_packages),
        os.path.realpath(dist_packages),
    ]


def test_receiver_bootstrap_adds_missing_site_packages(monkeypatch, tmp_path):
    current_site_packages = tmp_path / "current" / "lib" / "python3.11" / "site-packages"
    current_site_packages.mkdir(parents=True)
    inherited_site_packages = tmp_path / "shared" / "lib" / "python3.11" / "site-packages"
    inherited_site_packages.mkdir(parents=True)
    inherited_dist_packages = tmp_path / "shared" / "lib" / "python3" / "dist-packages"
    inherited_dist_packages.mkdir(parents=True)

    added = []
    monkeypatch.setattr(_dd_crashtracker_receiver.sys, "path", [str(current_site_packages)])
    monkeypatch.setenv(
        _dd_crashtracker_receiver._CRASHTRACKING_SITE_PACKAGES_ENV,
        os.pathsep.join(
            [
                str(current_site_packages),
                str(inherited_site_packages),
                str(inherited_dist_packages),
                str(inherited_site_packages),
            ]
        ),
    )
    monkeypatch.setattr(_dd_crashtracker_receiver.site, "addsitedir", added.append)

    _dd_crashtracker_receiver._bootstrap_receiver_site_packages()

    assert added == [
        os.path.realpath(inherited_site_packages),
        os.path.realpath(inherited_dist_packages),
    ]


def test_get_receiver_pythonpath_returns_ddtrace_parent(tmp_path):
    receiver_script = tmp_path / "site-packages" / "ddtrace" / "commands" / "_dd_crashtracker_receiver.py"
    receiver_script.parent.mkdir(parents=True)
    receiver_script.write_text("")

    assert crashtracking._get_receiver_pythonpath(str(receiver_script)) == os.path.realpath(tmp_path / "site-packages")


def test_get_args_uses_receiver_pythonpath_instead_of_parent_pythonpath(monkeypatch, tmp_path):
    receiver_script = tmp_path / "project" / "ddtrace" / "commands" / "_dd_crashtracker_receiver.py"
    receiver_script.parent.mkdir(parents=True)
    receiver_script.write_text("")
    shared_site_packages = tmp_path / "venv" / "lib" / "python3.11" / "site-packages"
    shared_site_packages.mkdir(parents=True)

    class DummyConfiguration(object):
        def __init__(self, *args):
            self.args = args

    class DummyReceiverConfig(object):
        def __init__(self, args, env, path_to_receiver_binary, stderr_filename, stdout_filename):
            self.args = args
            self.env = env
            self.path_to_receiver_binary = path_to_receiver_binary
            self.stderr_filename = stderr_filename
            self.stdout_filename = stdout_filename

    class DummyMetadata(object):
        def __init__(self, *args):
            self.args = args

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "bootstrap"))
    monkeypatch.setattr(
        crashtracking.importlib.util, "find_spec", lambda _: types.SimpleNamespace(origin=str(receiver_script))
    )
    monkeypatch.setattr(crashtracking, "CrashtrackerConfiguration", DummyConfiguration)
    monkeypatch.setattr(crashtracking, "CrashtrackerReceiverConfig", DummyReceiverConfig)
    monkeypatch.setattr(crashtracking, "CrashtrackerMetadata", DummyMetadata)
    monkeypatch.setattr(
        crashtracking,
        "StacktraceCollection",
        types.SimpleNamespace(
            Disabled="disabled",
            WithoutSymbols="without-symbols",
            EnabledWithSymbolsInReceiver="receiver",
            EnabledWithInprocessSymbols="inprocess",
        ),
    )
    monkeypatch.setattr(crashtracking.crashtracker_config, "stacktrace_resolver", None)
    monkeypatch.setattr(crashtracking.crashtracker_config, "create_alt_stack", False)
    monkeypatch.setattr(crashtracking.crashtracker_config, "use_alt_stack", False)
    monkeypatch.setattr(crashtracking.crashtracker_config, "debug_url", None)
    monkeypatch.setattr(crashtracking.crashtracker_config, "stderr_filename", None)
    monkeypatch.setattr(crashtracking.crashtracker_config, "stdout_filename", None)
    monkeypatch.setattr(crashtracking.agent_config, "trace_agent_url", "http://localhost:8126")
    monkeypatch.setattr(crashtracking.sys, "executable", "/tmp/python")
    monkeypatch.setattr(crashtracking.sys, "path", [str(shared_site_packages)])

    _, receiver_config, _ = crashtracking._get_args(None)

    assert receiver_config.env["PYTHONPATH"] == os.path.realpath(tmp_path / "project")
    assert receiver_config.env[crashtracking._CRASHTRACKING_SITE_PACKAGES_ENV] == os.path.realpath(shared_site_packages)
