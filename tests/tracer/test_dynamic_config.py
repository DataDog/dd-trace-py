import pytest

from ddtrace import config


# from ddtrace._config import remoteconfig_callback
# from ddtrace import Tracer


@pytest.fixture(autouse=True)
def reset_config():
    config.reset()
    yield
    config.reset()


def _base_config(cfg):
    lib_config = {
        "runtime_metrics_enabled": None,
        "tracing_debug": None,
        "tracing_http_header_tags": None,
        "tracing_service_mapping": None,
        "tracing_sample_rate": None,
        "tracing_sampling_rules": None,
        "span_sampling_rules": None,
        "data_streams_enabled": None,
    }
    lib_config.update(cfg)

    return {
        "action": "enable",
        "service_target": {"service": "", "env": ""},
        "lib_config": lib_config,
    }


@pytest.mark.parametrize(
    "config_sources",
    [
        {},
        {"env"},
        {"code"},
        {"rc"},
        {"env", "code"},
        {"env", "rc"},
        {"code", "rc"},
        {"env", "code", "rc"},
    ],
)
def test_service_mapping(config_sources, tracer, monkeypatch):
    if "env" in config_sources:
        monkeypatch.setenv("DD_SERVICE_MAPPING", "svc:env_svc")
        assert config.service_mapping == {"svc": "env_svc"}
        assert config._resolved_item("service_mapping") == {
            "source": "DD_SERVICE_MAPPING",
            "value": {"svc": "env_svc"},
            "raw": "svc:env_svc",
        }

    if "code" in config_sources:
        config.service_mapping = {"svc": "code_svc"}
        assert config._resolved_item("service_mapping") == {
            "source": "user",
            "value": {"svc": "code_svc"},
            "raw": {"svc": "code_svc"},
        }

    if "env" not in config_sources and "code" not in config_sources:
        assert config._resolved_item("service_mapping") == {
            "source": "default",
            "value": {},
            "raw": {},
        }
        assert config.service_mapping == {}

    with tracer.trace("before_config", service="svc") as span:
        pass

    # User code should take precedence over env.
    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": [{"from_key": "svc", "to_name": "rc_svc"}]}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "rc" in config_sources:
        assert span.service == "rc_svc"
    elif "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    # Unset the remote config to ensure it is reverted.
    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": None}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"


@pytest.mark.parametrize(
    "config_sources",
    [
        {},
        {"env"},
        # {"code"},
        # {"rc"},
        # {"env", "code"},
        # {"env", "rc"},
        # {"code", "rc"},
        # {"env", "code", "rc"},
    ],
)
def test_sample_rate(config_sources, tracer, monkeypatch):
    if "env" in config_sources:
        monkeypatch.setenv("DD_TRACE_SAMPLE_RATE", "0.25")
        assert config.trace_sample_rate == 0.25
        assert config._resolved_item("trace_sample_rate") == {
            "source": "DD_TRACE_SAMPLE_RATE",
            "value": 0.25,
            "raw": "0.25",
        }

    if "code" in config_sources:
        config.service_mapping = {"svc": "code_svc"}
        assert config._resolved_item("service_mapping") == {
            "source": "user",
            "value": {"svc": "code_svc"},
            "raw": {"svc": "code_svc"},
        }

    if "env" not in config_sources and "code" not in config_sources:
        assert config._resolved_item("service_mapping") == {
            "source": "default",
            "value": {},
            "raw": {},
        }
        assert config.service_mapping == {}

    with tracer.trace("before_config", service="svc") as span:
        pass

    # User code should take precedence over env.
    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": [{"from_key": "svc", "to_name": "rc_svc"}]}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "rc" in config_sources:
        assert span.service == "rc_svc"
    elif "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    # Unset the remote config to ensure it is reverted.
    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": None}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"
