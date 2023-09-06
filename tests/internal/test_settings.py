import pytest

from ddtrace import config


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
        {
            # DD_SERVICE takes precedence over all other env vars
            "env": {
                "DD_SERVICE": "dd_service",
                "DD_TAGS": "service:dd_tags_service",
                "GCP_PROJECT": "prj",
                "FUNCTION_NAME": "my_fn",
            },
            "expected_service": "dd_service",
        },
        {
            # DD_TAGS takes second-highest precedence over all other env vars
            "env": {"DD_TAGS": "service:dd_tags_service", "GCP_PROJECT": "prj", "FUNCTION_NAME": "my_fn"},
            "expected_service": "dd_tags_service",
        },
        {
            # Serverless should not work unless GCP or AWS is detected
            "env": {"FUNCTION_NAME": "my_fn"},
            "expected_service": None,
        },
        {
            "env": {"GCP_PROJECT": "prj", "FUNCTION_NAME": "my_fn"},
            "expected_service": "my_fn",
        },
        {
            # Test serverless precedence
            "env": {"GCP_PROJECT": "prj", "FUNCTION_NAME": "my_fn", "K_SERVICE": "my_svc"},
            "expected_service": "my_fn",
        },
        pytest.param(
            {"tag_code": "service:code_svc", "expected_service": "code_svc"},
            marks=pytest.mark.xfail(reason="The service isn't updated when tags are set programmatically"),
        ),
    ],
)
def test_service(test, monkeypatch):
    for env_name, env_value in test.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        # Reset the config to reload the env vars
        config.reset()

    if "code" in test:
        config.service = test["code"]

    if "tag_code" in test:
        config.tags = test["tag_code"]

    assert config.service == test["expected_service"]
