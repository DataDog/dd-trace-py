from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.internal import feature_flags


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    monkeypatch.setattr(feature_flags, "_implementation", None)


def _make_entry_point(dist_name, module_path, implementation):
    ep = MagicMock()
    ep.name = "openfeature"
    ep.group = feature_flags.GROUP
    ep.value = module_path
    ep.dist = MagicMock()
    ep.dist.metadata = {"Name": dist_name}
    ep.load.return_value = implementation
    return ep


def test_calls_are_delegated_to_the_loaded_provider():
    implementation = MagicMock()
    ep = _make_entry_point("ddtrace", "ddtrace.internal.openfeature._feature_flags", implementation)

    with patch("ddtrace.internal.feature_flags.get_entry_points", return_value=[ep]):
        feature_flags.enable_remote_config()
        feature_flags.set_domain_provider("domain")

    ep.load.assert_called_once_with()
    implementation.enable_remote_config.assert_called_once_with()
    implementation.set_domain_provider.assert_called_once_with("domain")


def test_untrusted_provider_is_not_loaded():
    implementation = MagicMock()
    evil_dist = _make_entry_point("evil-package", "ddtrace.internal.openfeature._feature_flags", implementation)
    evil_module = _make_entry_point("ddtrace", "evil.flags", implementation)

    with patch("ddtrace.internal.feature_flags.get_entry_points", return_value=[evil_dist, evil_module]):
        with pytest.raises(LookupError):
            feature_flags.set_domain_provider("domain")

    evil_dist.load.assert_not_called()
    evil_module.load.assert_not_called()
    implementation.set_domain_provider.assert_not_called()


def test_missing_provider_raises():
    with patch("ddtrace.internal.feature_flags.get_entry_points", return_value=[]):
        with pytest.raises(LookupError):
            feature_flags.enable_remote_config()


def test_failed_load_is_retried():
    implementation = MagicMock()
    ep = _make_entry_point("ddtrace", "ddtrace.internal.openfeature._feature_flags", implementation)
    ep.load.side_effect = [ImportError("transient"), implementation]

    with patch("ddtrace.internal.feature_flags.get_entry_points", return_value=[ep]):
        with pytest.raises(LookupError):
            feature_flags.set_domain_provider("domain")
        feature_flags.set_domain_provider("domain")

    assert ep.load.call_count == 2
    implementation.set_domain_provider.assert_called_once_with("domain")


def test_openfeature_provider_is_registered():
    from ddtrace.internal.openfeature import _feature_flags

    assert feature_flags._get_implementation() is _feature_flags
