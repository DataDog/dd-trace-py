from unittest.mock import patch

import pytest

from ddtrace.llmobs import LLMObs
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if LLMObs.enabled:
        LLMObs.disable()
    LLMObs._ff_prompt_serving = False
    from ddtrace.internal.settings.openfeature import config as ffe_config

    ffe_config.experimental_flagging_provider_enabled = False


class TestFFPromptServing:
    def test_enable_with_prompt_serving_local(self):
        with override_global_config(dict(_dd_api_key="test-key", _llmobs_ml_app="test-app")):
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc") as mock_enable_rc:
                LLMObs.enable(prompt_serving="local", agentless_enabled=False)
                mock_enable_rc.assert_called_once()

            assert LLMObs._ff_prompt_serving is True

            from ddtrace.internal.settings.openfeature import config as ffe_config

            assert ffe_config.experimental_flagging_provider_enabled is True

    def test_enable_without_prompt_serving(self):
        with override_global_config(dict(_dd_api_key="test-key", _llmobs_ml_app="test-app")):
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc") as mock_enable_rc:
                LLMObs.enable(agentless_enabled=False)
                mock_enable_rc.assert_not_called()

            assert LLMObs._ff_prompt_serving is False

    def test_disable_resets_ff_prompt_serving(self):
        with override_global_config(dict(_dd_api_key="test-key", _llmobs_ml_app="test-app")):
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc"):
                LLMObs.enable(prompt_serving="local", agentless_enabled=False)

            assert LLMObs._ff_prompt_serving is True
            LLMObs.disable()
            assert LLMObs._ff_prompt_serving is False
