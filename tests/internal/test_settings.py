import pytest

from ddtrace import config


# Reset the configuration between each test case.
# This is necessary because the config object is a singleton.
@pytest.fixture(autouse=True)
def reset_config():
    config.reset()
    yield
    config.reset()


@pytest.mark.parametrize(
    "test",
    [
        {
            "expected_service": None,
        },
        {
            "env": {"DD_SERVICE": "env_svc"},
            "expected_service": "env_svc",
        },
        {
            "env": {"DD_TAGS": "service:env_svc"},
            "expected_service": "env_svc",
        },
        {
            "code": "code_svc",
            "expected_service": "code_svc",
        },
        {
            "env": {"DD_SERVICE": "env_svc1", "DD_TAGS": "service:env_svc2"},
            "expected_service": "env_svc1",
        },
        {
            "env": {"DD_SERVICE": "env_svc1", "DD_TAGS": "service:env_svc2"},
            "code": "code_svc",
            "expected_service": "code_svc",
        },
    ],
)
def test_service(test, monkeypatch):
    for env_name, env_value in test.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
    if "code" in test:
        config.service = test["code"]

    assert config.service == test["expected_service"]
