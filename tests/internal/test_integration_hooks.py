from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.internal import integration_hooks


@pytest.fixture(autouse=True)
def _reset_hooks(monkeypatch):
    monkeypatch.setattr(integration_hooks, "_entry_points", None)
    monkeypatch.setattr(integration_hooks, "_loaded", {})


def _make_entry_point(name, dist_name, module_path, hooks):
    ep = MagicMock()
    ep.name = name
    ep.group = integration_hooks.GROUP
    ep.value = module_path
    ep.dist = MagicMock()
    ep.dist.metadata = {"Name": dist_name}
    ep.load.return_value = hooks
    return ep


def test_on_patch_and_unpatch_call_hooks():
    hooks = MagicMock()
    ep = _make_entry_point("anthropic", "ddtrace", "ddtrace.llmobs._contrib.anthropic", hooks)

    with patch("ddtrace.internal.integration_hooks.get_entry_points", return_value=[ep]):
        integration_hooks.on_patch("anthropic")
        integration_hooks.on_unpatch("anthropic")

    hooks.listen.assert_called_once_with()
    hooks.unlisten.assert_called_once_with()


def test_hooks_load_only_for_patched_integration():
    anthropic_ep = _make_entry_point("anthropic", "ddtrace", "ddtrace.llmobs._contrib.anthropic", MagicMock())
    openai_ep = _make_entry_point("openai", "ddtrace", "ddtrace.llmobs._contrib.openai", MagicMock())

    with patch("ddtrace.internal.integration_hooks.get_entry_points", return_value=[anthropic_ep, openai_ep]):
        integration_hooks.on_patch("anthropic")
        integration_hooks.on_patch("anthropic")

    anthropic_ep.load.assert_called_once_with()
    openai_ep.load.assert_not_called()


def test_untrusted_hooks_are_not_loaded():
    hooks = MagicMock()
    evil_dist = _make_entry_point("anthropic", "evil-package", "ddtrace.llmobs._contrib.anthropic", hooks)
    evil_module = _make_entry_point("anthropic", "ddtrace", "evil.hooks", hooks)

    with patch("ddtrace.internal.integration_hooks.get_entry_points", return_value=[evil_dist, evil_module]):
        integration_hooks.on_patch("anthropic")

    evil_dist.load.assert_not_called()
    evil_module.load.assert_not_called()
    hooks.listen.assert_not_called()


def test_failing_hook_does_not_break_patching():
    broken = _make_entry_point("anthropic", "ddtrace", "ddtrace.llmobs._contrib.broken", None)
    broken.load.side_effect = ImportError("boom")
    raising = MagicMock()
    raising.listen.side_effect = RuntimeError("boom")
    working = MagicMock()
    eps = [
        broken,
        _make_entry_point("anthropic", "ddtrace", "ddtrace.llmobs._contrib.raising", raising),
        _make_entry_point("anthropic", "ddtrace", "ddtrace.llmobs._contrib.anthropic", working),
    ]

    with patch("ddtrace.internal.integration_hooks.get_entry_points", return_value=eps):
        integration_hooks.on_patch("anthropic")

    working.listen.assert_called_once_with()


def test_anthropic_hook_is_registered():
    # Guards the pyproject.toml declaration: without it, a direct patch() never
    # registers the LLMObs subscribers and Anthropic spans lose their shadow tags.
    plugins = integration_hooks.get_entry_points(integration_hooks.GROUP)
    assert any(p.name == "anthropic" and p.value == "ddtrace.llmobs._contrib.anthropic" for p in plugins)
