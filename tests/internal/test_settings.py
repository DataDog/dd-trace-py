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
                "trace_sample_rate": 1.0,
                "logs_injection": False,
                "trace_http_header_tags": {},
            }
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "expected": {
                "trace_sample_rate": 0.9,
            }
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "code": {
                "trace_sample_rate": 0.8,
            },
            "expected": {
                "trace_sample_rate": 0.8,
            }
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "expected": {"logs_injection": True},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "code": {"logs_injection": False},
            "expected": {"logs_injection": False},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "expected": {"trace_http_header_tags": {"X-Header-Tag-1": "header_tag_1", "X-Header-Tag-2": "header_tag_2"}},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "code": {"trace_http_header_tags": {"header": "value"}},
            "expected": {"trace_http_header_tags": {"header": "value"}},
        },
        # xfail
        # {
        #     "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1,X-Header-Tag-2"},
        #     "expected": {"trace_http_header_tags": {"X-Header-Tag-1": "", "X-Header-Tag-2": ""}},
        # },
    ],
)
def test_settings(testcase, config, monkeypatch):
    for env_name, env_value in testcase.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        config.reset()

    for code_name, code_value in testcase.get("code", {}).items():
        setattr(config, code_name, code_value)

    for expected_name, expected_value in testcase["expected"].items():
        assert getattr(config, expected_name) == expected_value


def test_subscription(config):
    for s in ("trace_sample_rate", "logs_injection", "trace_http_header_tags"):
        _handler = mock.MagicMock()
        config._subscribe([s], _handler)
        setattr(config, s, "1")
        _handler.assert_called_once_with(config, [s])
