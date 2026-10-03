"""Regression tests for the OpenFeature test helper."""

from openfeature import api
from openfeature.exception import ProviderNotReadyError
import pytest

from tests.openfeature.conftest import set_openfeature_provider


@pytest.mark.skipif(not hasattr(api, "set_provider_and_wait"), reason="Blocking registration requires SDK 0.10+")
def test_set_openfeature_provider_swallows_no_config_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixtures register before loading FFE config; a timeout must not fail setup."""
    called: list[object] = []

    def _raise(provider: object, *args: object, **kwargs: object) -> None:
        called.append(provider)
        raise ProviderNotReadyError("No Feature Flagging configuration received before the initialization timeout")

    monkeypatch.setattr(api, "set_provider_and_wait", _raise)
    sentinel: object = object()
    set_openfeature_provider(sentinel)
    assert called == [sentinel]
