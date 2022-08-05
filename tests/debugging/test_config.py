from contextlib import contextmanager

from ddtrace.debugging._config import DebuggerConfig
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.version import get_version
from tests.utils import override_env


@contextmanager
def debugger_config(**kwargs):
    with override_env(kwargs):
        from ddtrace.settings import Config
        import ddtrace.settings.debugger

        old_config = ddtrace.settings.debugger.config
        ddtrace.settings.debugger.config = Config()

        yield DebuggerConfig()

        ddtrace.settings.debugger.config = old_config


def test_tags():
    with debugger_config(DD_TAGS="a:b,c:d", DD_ENV="test-env", DD_VERSION="test-version") as config:
        tags = parse_tags_str(config.tags)

        assert tags == dict(
            a="b",
            c="d",
            env="test-env",
            version="test-version",
            debugger_version=get_version(),
        )


def test_snapshot_intake_url():
    DebuggerConfig()._snapshot_intake_url == get_trace_url()


def test_service_name():
    assert DebuggerConfig().service_name == get_application_name()

    with debugger_config(DD_SERVICE="test-service") as config:
        assert config.service_name == "test-service"
