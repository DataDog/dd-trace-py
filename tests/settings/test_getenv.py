from ddtrace.settings.matching import FuzzyEnvMatcher
from tests.utils import override_env


def test_log_env_var(caplog):
    value = "agent host value"
    with override_env({"DD_AGENT_HOST": value}):
        env = FuzzyEnvMatcher()
        assert env.get("DD_AGENT_HOST") == value
        assert caplog.record_tuples == []


def test_log_env_var_typo(caplog):
    value = "agent host value"
    with override_env({"DD_AGENT_HOST": value}):
        env = FuzzyEnvMatcher()

        assert env.get("DD_AGENT_HSOT") is None
        assert caplog.record_tuples == [
            ("ddtrace.settings.matching", 30, "Env variable DD_AGENT_HSOT not recognized, did you mean DD_AGENT_HOST")
        ]


def test_log_env_var_different_value(caplog):
    value = "agent host value"
    with override_env({"DD_AGENT_HOST": value}):
        env = FuzzyEnvMatcher()

        assert env.get("DD_TRACE_ENABLED") is None
        assert caplog.record_tuples == []
