"""Helpers shared by the flag selection test modules."""

from contextlib import contextmanager
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
import pytest

from ddtrace.internal.openfeature import _flag_selection
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import claim_process_selection
from ddtrace.internal.openfeature._source_selection import create_agentless_source
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


MANAGED_TARGET = "/api/v2/feature-flagging/config/rules-based/server?dd_env=prod"


SELECTION_LOGGER = "ddtrace.internal.openfeature._flag_selection"


PROVIDER_LOGGER = "ddtrace.internal.openfeature._provider"


IGNORED = "flag_selection was ignored by the server ({}). Loaded the full flag configuration."


REMOTE_CONFIG_WARNING = (
    "flag_selection is not supported when flags are delivered through Remote Config. "
    "Loading the full flag configuration."
)


SECOND_SELECTION_WARNING = (
    "flag_selection differs from the flag selection already in use in this process. Using the first selection."
)


def _config(**env):
    return OpenFeatureConfig(source=env)


def _create_source(flag_selection=None, base_url=None, **env):
    if base_url is not None:
        env["DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL"] = base_url
    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "env": "prod"}):
        return create_agentless_source(_config(**env), lambda _: None, canonicalize_flag_selection(flag_selection))


def _sent_selection(source):
    """The selection a source requests, as (tags, key patterns) in request order."""
    pairs = parse_qsl(urlsplit(source._request_target).query, keep_blank_values=True)
    return (
        [value for name, value in pairs if name == "selected_flag_tags"],
        [value for name, value in pairs if name == "selected_flag_key_patterns"],
    )


def _not_sent(caplog):
    return [
        r
        for r in caplog.records
        if r.name == SELECTION_LOGGER and r.getMessage().startswith("flag_selection was not sent")
    ]


def _metric_calls(count_metric, name):
    return [c for c in count_metric.call_args_list if c.args[1] == name]


class _ExplodingMapping(dict):
    def get(self, key, default=None):
        raise RuntimeError("boom")


class _ExplodingList(list):
    def __iter__(self):
        raise RuntimeError("boom")


MALFORMED_OPTIONS = [
    "tags",
    b"tags",
    ["a"],
    12,
    object(),
    {"tags": "abc"},
    {"key_patterns": "checkout.*"},
    {"tags": {"a": 1}},
    {"tags": [1]},
    {"tags": [None]},
    {"tags": [b"a"]},
    {"tags": [True]},
    {"key_patterns": ["\ud800"]},
    {"tags": ["a\udfffb"]},
    {"tags": ["a"] * 40, "key_patterns": [None]},
    {"tags": ["a" * 201], "key_patterns": [3]},
    {"tags": [f"v{i}" for i in range(32)] + ["\ud800"]},
    {"tags": ["\udc00"], "key_patterns": ["x" * 201]},
]


EXPLODING_OPTIONS = [_ExplodingMapping(tags=["a"]), {"tags": _ExplodingList(["a"])}]


def _option_with_url_length(endpoint, length):
    """A valid selection whose request URL is exactly ``length`` bytes long."""
    remaining = length - len(endpoint)
    tags = [f"tag{i:02d}".ljust(200, "x") for i in range(31)]
    remaining -= sum(len("&selected_flag_tags=") + len(tag) for tag in tags)
    overhead = len("&selected_flag_key_patterns=")
    key_patterns = []
    while remaining > overhead + 200:
        key_patterns.append(f"pattern{len(key_patterns):02d}".ljust(200, "x"))
        remaining -= overhead + 200
    last = remaining - overhead
    assert 9 <= last <= 200
    key_patterns.append(f"pattern{len(key_patterns):02d}".ljust(last, "x"))
    return {"tags": tags, "key_patterns": key_patterns}


def _ufc_body():
    return json.dumps(
        {"data": {"id": "1", "type": "universal-flag-configuration", "attributes": create_config()}}
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        # A list value is a header repeated on several lines.
        self._headers = [
            (name.lower(), value)
            for name, values in (headers or {}).items()
            for value in (values if isinstance(values, list) else [values])
        ]
        self.read_headers: list = []

    def read(self):
        return self._body

    def getheader(self, name, default=None):
        values = self._get_all(name)
        return values[0] if values else default

    @property
    def headers(self):
        return SimpleNamespace(get_all=self._get_all)

    def _get_all(self, name, failobj=None):
        self.read_headers.append(name.lower())
        return [value for key, value in self._headers if key == name.lower()] or failobj


class _FakeConn:
    def __init__(self, respond, requests):
        self._respond = respond
        self._requests = requests
        self._target = None

    def request(self, method, target, body, headers):
        self._target = target
        self._requests.append(target)

    def getresponse(self):
        return self._respond(self._target)

    def close(self):
        pass


def _flag_body(flag_key):
    return json.dumps(
        {
            "data": {
                "id": "1",
                "type": "universal-flag-configuration",
                "attributes": create_config(create_boolean_flag(flag_key, enabled=True, default_value=True)),
            }
        }
    ).encode("utf-8")


def _provider(flag_selection, timeout=5.0):
    return DataDogProvider(initialization_timeout=timeout, flag_selection=flag_selection)


class _SignalingEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()

    def wait(self, timeout=None):
        self.waiting.set()
        return super().wait(timeout)


def _initialize_quietly(provider):
    try:
        provider.initialize(EvaluationContext())
    except ProviderNotReadyError:
        pass


def _poll_without_configuration(provider, cdn, monkeypatch):
    """Initialize provider while the CDN has no configuration, so it times out and keeps polling every 0.2 s."""
    from ddtrace.internal.openfeature import _provider as provider_module

    monkeypatch.setattr(provider_module.ffe_config, "configuration_source_agentless_poll_interval_seconds", 0.2)
    cdn.respond = lambda target: _FakeResponse(404)
    with pytest.raises(ProviderNotReadyError):
        provider.initialize(EvaluationContext())
    return provider._configuration_source


def _on_recovery(provider, cdn, monkeypatch, handler):
    """Serve configuration, so the poller emits PROVIDER_READY and runs handler on its own thread, in its poll.

    openfeature-sdk 0.8.4 runs PROVIDER_READY handlers on the emitting thread, as this does.
    """
    monkeypatch.setattr(provider, "emit_provider_ready", handler)
    cdn.respond = lambda target: _FakeResponse(200, _ufc_body())


@contextmanager
def _serving_cdn(monkeypatch):
    """Serve every agentless poll from a swappable responder and record request targets."""

    class _Cdn:
        requests: list = []

        def __init__(self):
            self.respond = lambda target: _FakeResponse(200, _ufc_body())

    server = _Cdn()
    monkeypatch.setattr(
        source_mod,
        "get_connection",
        lambda url, timeout=None: _FakeConn(lambda target: server.respond(target), server.requests),
    )
    _set_ffe_config(None)
    try:
        yield server
    finally:
        _set_ffe_config(None)


@contextmanager
def _agentless_api_key():
    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "env": None}):
        yield


def _child_dropped_counts(flag_selection):
    """Start a source for ``flag_selection``, fork, and return the dropped counts the child records."""
    import os

    with (
        mock.patch.object(_flag_selection, "log"),
        mock.patch.object(_flag_selection, "telemetry_writer"),
    ):
        _create_source(flag_selection)

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        with (
            mock.patch.object(_flag_selection, "log") as log,
            mock.patch.object(_flag_selection, "telemetry_writer") as telemetry_writer,
        ):
            _flag_selection.count_inherited_drop()
            _flag_selection.count_inherited_drop()
        calls = telemetry_writer.add_count_metric.call_args_list
        result = {"counts": [list(c.args[1:2]) + [dict(c.args[3])] for c in calls]}
        result["logged"] = log.error.call_count + log.warning.call_count
        os.write(write_fd, json.dumps(result).encode())
        os._exit(0)

    os.close(write_fd)
    with os.fdopen(read_fd) as reader:
        output = reader.read()
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    return json.loads(output)


def _forked_child_status(check):
    """Fork, run ``check`` in the child, and return the child's exit status: 0 when ``check`` returns True."""
    import os
    import tempfile
    import traceback
    import warnings

    with tempfile.NamedTemporaryFile(prefix="fork-child-", delete=False) as report:
        path = report.name
    with warnings.catch_warnings():
        # CPython 3.12+ warns when a multi-threaded process forks.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        try:
            os._exit(0 if check() else 1)
        except BaseException:
            with open(path, "w") as out:
                out.write(traceback.format_exc())
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    with open(path) as written:
        raised = written.read()
    os.unlink(path)
    # The child's stderr does not reach every test runner's capture, so its traceback is reported from here.
    assert not raised, raised
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1


def _poller_runs(source, timeout):
    """Whether the source's poller thread runs within ``timeout``.

    In a forked child the thread module restarts inherited periodic threads after a 100 ms quiet period.
    """
    from ddtrace.internal._threads import periodic_threads

    deadline = time.monotonic() + timeout
    while True:
        if any(thread is source._worker for thread in list(periodic_threads.values())):
            return True
        if time.monotonic() >= deadline:
            return False
        threading.Event().wait(0.02)


def _child_selection_released(provider):
    """Stop the inherited provider's poller in the child and check that the next claim takes a new selection.

    Only the poller is stopped: the fork may land while the initializing thread is still starting the writers.
    """
    other = canonicalize_flag_selection({"tags": ["b"]})
    provider._stop_configuration_source()
    released = _flag_selection._claim_count == 0 and claim_process_selection(other) == other
    _flag_selection.release_process_selection()
    return released


def _fork_during(hold_released_by, monkeypatch, wait_expires, check):
    """Fork while a start or teardown is held, letting it finish during the fork's wait unless the wait expires."""
    import ddtrace.internal.openfeature._provider as provider_module

    if wait_expires:
        monkeypatch.setattr(provider_module, "_FORK_WAIT_SECONDS", 0.05)
        status = _forked_child_status(check)
        hold_released_by.set()
    else:
        # The fork is already waiting when the held operation is let go; a slow runner must not make it give up.
        monkeypatch.setattr(provider_module, "_FORK_WAIT_SECONDS", 5.0)
        threading.Timer(0.2, hold_released_by.set).start()
        status = _forked_child_status(check)
    return status
