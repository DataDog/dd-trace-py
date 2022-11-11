import pytest

from ddtrace.settings import _database_monitoring
from tests.utils import override_env


def test_propagation_mode_configuration():
    # Ensure Database Monitoring support is disabled by default
    # TODO: Enable DBM support by default
    assert _database_monitoring.dbm_config.propagation_mode == "disabled"

    # Ensure service is a valid injection mode
    with override_env(dict(DD_DBM_PROPAGATION_MODE="service")):
        config = _database_monitoring.DatabaseMonitoringConfig()
        assert config.propagation_mode == "service"

    # Ensure full is a valid injection mode
    with override_env(dict(DD_DBM_PROPAGATION_MODE="full")):
        config = _database_monitoring.DatabaseMonitoringConfig()
        assert config.propagation_mode == "full"

    # Ensure an invalid injection mode raises a ValueError
    with override_env(dict(DD_DBM_PROPAGATION_MODE="notaninjectionmode")):
        with pytest.raises(ValueError) as excinfo:
            _database_monitoring.DatabaseMonitoringConfig()
    assert (
        excinfo.value.args[0] == "Invalid value for environment variable DD_DBM_PROPAGATION_MODE: "
        "value must be one of ['disabled', 'full', 'service']"
    )


@pytest.mark.subprocess(env=dict(DD_DBM_PROPAGATION_MODE="disabled"))
def test_get_dbm_comment_disabled_mode():
    from ddtrace import tracer
    from ddtrace.settings import _database_monitoring

    dbspan = tracer.trace("dbspan", service="orders-db")
    sqlcomment = _database_monitoring._get_dbm_comment(dbspan)
    assert sqlcomment is None
    # ensure that dbm tag is not set (only required in full mode)
    assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) is None


@pytest.mark.subprocess(
    env=dict(
        DD_DBM_PROPAGATION_MODE="service",
        DD_SERVICE="orders-app",
        DD_ENV="staging",
        DD_VERSION="v7343437-d7ac743",
    )
)
def test_get_dbm_comment_service_mode():
    from ddtrace import tracer
    from ddtrace.settings import _database_monitoring

    dbspan = tracer.trace("dbname", service="orders-db")

    sqlcomment = _database_monitoring._get_dbm_comment(dbspan)
    # assert tags sqlcomment contains the correct value
    assert sqlcomment == " /*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743'*/"
    # ensure that dbm tag is not set (only required in full mode)
    assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) is None


@pytest.mark.subprocess(
    env=dict(
        DD_DBM_PROPAGATION_MODE="full",
        DD_SERVICE="orders-app",
        DD_ENV="staging",
        DD_VERSION="v7343437-d7ac743",
    )
)
def test_get_dbm_comment_full_mode():
    from ddtrace import tracer
    from ddtrace.settings import _database_monitoring

    dbspan = tracer.trace("dbname", service="orders-db")

    sqlcomment = _database_monitoring._get_dbm_comment(dbspan)
    # assert tags sqlcomment contains the correct value
    assert (
        sqlcomment
        == " /*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743',traceparent='%s'*/"
        % (dbspan.context._traceparent,)
    )
    # ensure that dbm tag is set (only required in full mode)
    assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) == "true"
