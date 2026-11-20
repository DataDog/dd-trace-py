"""Helpers shared by the flag selection test modules."""

import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
import pytest

from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
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
