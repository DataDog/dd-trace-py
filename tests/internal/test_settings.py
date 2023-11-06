import mock
import pytest

from ddtrace.settings import Config


@pytest.fixture
def config():
    yield Config()


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "expected": {
                "_trace_sample_rate": 1.0,
                "logs_injection": False,
                "trace_http_header_tags": {},
            },
            "expected_source": {
                "_trace_sample_rate": "default",
                "logs_injection": "default",
                "trace_http_header_tags": "default",
            },
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "expected": {"_trace_sample_rate": 0.9},
            "expected_source": {"_trace_sample_rate": "env"},
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "code": {"_trace_sample_rate": 0.8},
            "expected": {"_trace_sample_rate": 0.8},
            "expected_source": {"_trace_sample_rate": "code"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "expected": {"logs_injection": True},
            "expected_source": {"logs_injection": "env"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "code": {"logs_injection": False},
            "expected": {"logs_injection": False},
            "expected_source": {"logs_injection": "code"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "expected": {
                "trace_http_header_tags": {"X-Header-Tag-1": "header_tag_1", "X-Header-Tag-2": "header_tag_2"}
            },
            "expected_source": {"trace_http_header_tags": "env"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "code": {"trace_http_header_tags": {"header": "value"}},
            "expected": {"trace_http_header_tags": {"header": "value"}},
            "expected_source": {"trace_http_header_tags": "code"},
        },
    ],
)
def test_settings(testcase, config, monkeypatch):
    for env_name, env_value in testcase.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        config._reset()

    for code_name, code_value in testcase.get("code", {}).items():
        setattr(config, code_name, code_value)

    for expected_name, expected_value in testcase["expected"].items():
        assert getattr(config, expected_name) == expected_value

    for expected_name, expected_source in testcase.get("expected_source", {}).items():
        assert config._get_source(expected_name) == expected_source


def test_config_subscription(config):
    for s in ("_trace_sample_rate", "logs_injection", "trace_http_header_tags"):
        _handler = mock.MagicMock()
        config._subscribe([s], _handler)
        setattr(config, s, "1")
        _handler.assert_called_once_with(config, [s])
