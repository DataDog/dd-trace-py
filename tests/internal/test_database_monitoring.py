import pytest

from ddtrace.settings import database_monitoring
from tests.utils import override_env


def test_injection_mode_configuration():
    # Ensure Database Monitoring support is disabled by default
    # TODO: Enable DBM support by default
    assert database_monitoring.config.injection_mode == "disabled"

    # Ensure service is a valid injection mode
    with override_env(dict(DD_TRACE_SQL_COMMENT_INJECTION_MODE="service")):
        config = database_monitoring.DatabaseMonitoringConfig()
        assert config.injection_mode == "service"

    # Ensure full is a valid injection mode
    with override_env(dict(DD_TRACE_SQL_COMMENT_INJECTION_MODE="full")):
        config = database_monitoring.DatabaseMonitoringConfig()
        assert config.injection_mode == "full"

    # Ensure an invalid injection mode raises a ValueError
    with override_env(dict(DD_TRACE_SQL_COMMENT_INJECTION_MODE="notaninjectionmode")):
        with pytest.raises(ValueError) as excinfo:
            database_monitoring.DatabaseMonitoringConfig()
    assert (
        excinfo.value.args[0] == "Invalid value for environment variable DD_TRACE_SQL_COMMENT_INJECTION_MODE: "
        "value must be one of ['disabled', 'full', 'service']"
    )
