from unittest.mock import patch

from ddtrace.llmobs import LLMObs


class TestFFPromptServing:
    def _reset(self):
        if LLMObs.enabled:
            LLMObs.disable()
        LLMObs._ff_prompt_serving = False

    def test_enable_with_prompt_serving_local(self):
        self._reset()
        try:
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc") as mock_enable_rc:
                LLMObs.enable(prompt_serving="local", agentless_enabled=False)
                mock_enable_rc.assert_called_once()

            assert LLMObs._ff_prompt_serving is True

            from ddtrace.internal.settings.openfeature import config as ffe_config

            assert ffe_config.experimental_flagging_provider_enabled is True
        finally:
            self._reset()
            from ddtrace.internal.settings.openfeature import config as ffe_config

            ffe_config.experimental_flagging_provider_enabled = False

    def test_enable_without_prompt_serving(self):
        self._reset()
        try:
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc") as mock_enable_rc:
                LLMObs.enable(agentless_enabled=False)
                mock_enable_rc.assert_not_called()

            assert LLMObs._ff_prompt_serving is False
        finally:
            self._reset()

    def test_disable_resets_ff_prompt_serving(self):
        self._reset()
        try:
            with patch("ddtrace.internal.openfeature._remoteconfiguration.enable_featureflags_rc"):
                LLMObs.enable(prompt_serving="local", agentless_enabled=False)

            assert LLMObs._ff_prompt_serving is True
            LLMObs.disable()
            assert LLMObs._ff_prompt_serving is False
        finally:
            self._reset()
            from ddtrace.internal.settings.openfeature import config as ffe_config

            ffe_config.experimental_flagging_provider_enabled = False
