import pytest

from ddtrace.debugging._config import DEFAULT_PROBE_API_URL
from ddtrace.debugging._config import DebuggerConfig
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.version import get_version
from tests.utils import override_env


@pytest.mark.parametrize(
    "dd_site, probe_api_url",
    [
        ("datadoghq.com", "https://app.datadoghq.com"),
        ("datadoghq.eu", "https://app.datadoghq.eu"),
        ("", DEFAULT_PROBE_API_URL),
    ],
)
def test_probe_api_url(dd_site, probe_api_url):
    with override_env(dict(DD_SITE=dd_site)):
        DebuggerConfig().probe_api_url == probe_api_url


def test_tags():
    with override_env(dict(DD_TAGS="a:b,c:d", DD_ENV="test-env", DD_VERSION="test-version")):
        import ddtrace.debugging._config
        from ddtrace.settings import Config

        old_config = ddtrace.debugging._config.tracer_config
        ddtrace.debugging._config.tracer_config = Config()

        tags = parse_tags_str(DebuggerConfig().tags)
        assert tags == dict(
            a="b",
            c="d",
            env="test-env",
            version="test-version",
            debugger_version=get_version(),
        )

        ddtrace.debugging._config.tracer_config = old_config


def test_snapshot_intake_url():
    DebuggerConfig().snapshot_intake_url == "http://localhost:8126"


def test_agent_mode():
    assert DebuggerConfig()._agent is False

    with override_env(dict(DD_DEBUGGER_AGENT_MODE="1")):
        assert DebuggerConfig()._agent is True
