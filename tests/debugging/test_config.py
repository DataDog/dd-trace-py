from contextlib import contextmanager

from ddtrace.debugging._config import DynamicInstrumentationConfig
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.version import get_version
from tests.utils import override_env


@contextmanager
def debugger_config(**kwargs):
    with override_env(kwargs):
        from ddtrace.settings import Config
        import ddtrace.settings.dynamic_instrumentation

        old_config = ddtrace.settings.dynamic_instrumentation.config
        ddtrace.settings.dynamic_instrumentation.config = Config()

        yield DynamicInstrumentationConfig()

        ddtrace.settings.dynamic_instrumentation.config = old_config


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
    DynamicInstrumentationConfig()._snapshot_intake_url == get_trace_url()


def test_service_name():
    assert DynamicInstrumentationConfig().service_name == get_application_name()

    with debugger_config(DD_SERVICE="test-service") as config:
        assert config.service_name == "test-service"
