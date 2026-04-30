import logging
import os
import sys

import pytest

# Acquire the env submodule via sys.modules because
# ddtrace.internal.settings.__init__ re-exports dd_environ as `env`, which
# shadows the submodule name for normal `import ... as ...` forms.
from ddtrace.internal.settings.env import dd_environ  # noqa: I100


env_module = sys.modules["ddtrace.internal.settings.env"]
ENV_LOGGER = env_module.__name__


@pytest.fixture(autouse=True)
def reset_warned_keys():
    env_module._warned_keys.clear()
    yield
    env_module._warned_keys.clear()


def test_registered_key_no_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_AGENT_HOST")
    assert caplog.text == ""


def test_unregistered_dd_key_logs_once(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
    assert caplog.text.count("DD_TOTALLY_BOGUS_XYZ") == 1
    assert "Unsupported Datadog configuration variable accessed" in caplog.text


def test_unregistered_otel_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("OTEL_TOTALLY_BOGUS_XYZ")
    assert "OTEL_TOTALLY_BOGUS_XYZ" in caplog.text


def test_non_dd_otel_key_skipped(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("PATH")
        env_module._validate_key("HOME")
        env_module._validate_key("_DD_INTERNAL_VAR")
    assert caplog.text == ""


def test_alias_is_accepted(caplog):
    # Pick an arbitrary registered alias from the generated registry
    alias = next(iter(env_module._ALIAS_TARGETS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(alias)
    assert caplog.text == ""


def test_deprecated_key_logs_deprecated_message(caplog):
    if not env_module.DEPRECATED_CONFIGURATIONS:
        pytest.skip("no deprecated configurations registered")
    deprecated_key = next(iter(env_module.DEPRECATED_CONFIGURATIONS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(deprecated_key)
    assert "Deprecated Datadog configuration variable accessed" in caplog.text
    assert deprecated_key in caplog.text


def test_getitem_reads_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "myhost.example.com")
    assert dd_environ["DD_AGENT_HOST"] == "myhost.example.com"


def test_getitem_raises_keyerror_when_missing(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    with pytest.raises(KeyError):
        dd_environ["DD_AGENT_HOST"]


def test_setitem_writes_to_os_environ(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    dd_environ["DD_AGENT_HOST"] = "set-via-dd-environ"
    try:
        assert os.environ["DD_AGENT_HOST"] == "set-via-dd-environ"
    finally:
        del os.environ["DD_AGENT_HOST"]


def test_delitem_removes_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    del dd_environ["DD_AGENT_HOST"]
    assert "DD_AGENT_HOST" not in os.environ


def test_contains_true_for_set_key(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    assert "DD_AGENT_HOST" in dd_environ


def test_contains_false_for_unset_key(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    assert "DD_AGENT_HOST" not in dd_environ


def test_contains_warns_on_unregistered_key(caplog, monkeypatch):
    monkeypatch.delenv("DD_UNREGISTERED_CONTAINS_TEST", raising=False)
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        result = "DD_UNREGISTERED_CONTAINS_TEST" in dd_environ
    assert result is False
    assert "DD_UNREGISTERED_CONTAINS_TEST" in caplog.text


def test_len_and_iter_match_os_environ():
    assert len(dd_environ) == len(os.environ)
    assert set(dd_environ) == set(os.environ)


def test_copy_returns_plain_dict():
    snapshot = dd_environ.copy()
    assert isinstance(snapshot, dict)
    assert snapshot == dict(os.environ)
