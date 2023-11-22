from contextlib import contextmanager

import pytest

from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.utils.config import get_application_name
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings.dynamic_instrumentation import DynamicInstrumentationConfig
from ddtrace.version import get_version
from tests.utils import override_env


@contextmanager
def debugger_config(**kwargs):
    with override_env(kwargs):
        from ddtrace.settings import Config
        import ddtrace.settings.dynamic_instrumentation

        old_config = ddtrace.settings.dynamic_instrumentation.ddconfig
        old_di_config = ddtrace.settings.dynamic_instrumentation.config.__dict__

        try:
            ddtrace.settings.dynamic_instrumentation.ddconfig = Config()
            new_config = DynamicInstrumentationConfig()
            ddtrace.settings.dynamic_instrumentation.config.__dict__ = new_config.__dict__

            yield ddtrace.settings.dynamic_instrumentation.config

        finally:
            ddtrace.settings.dynamic_instrumentation.config.__dict__ = old_di_config
            ddtrace.settings.dynamic_instrumentation.ddconfig = old_config


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
    assert DynamicInstrumentationConfig()._intake_url == get_trace_url()


def test_service_name():
    assert DynamicInstrumentationConfig().service_name == get_application_name()

    with debugger_config(DD_SERVICE="test-service") as config:
        assert config.service_name == "test-service"


def test_redacted_identifiers():
    with debugger_config(DD_DYNAMIC_INSTRUMENTATION_REDACTED_IDENTIFIERS="foo , bar") as config:
        assert config.redacted_identifiers == frozenset(["foo", "bar"])


def test_redacted_types():
    with debugger_config(DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES="Foo, Bar") as config:
        assert config.redacted_types == frozenset(["Foo", "Bar"])


def test_redacted_types_invalid_pattern():
    with pytest.raises(ValueError):
        with debugger_config(DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES="Foo()"):
            pass


def test_redacted_types_matching():
    with debugger_config(DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES="Secret*") as config:
        assert config.redacted_types_re.search("Secret")
        assert not config.redacted_types_re.search("NotSecret")
