import logging

import pytest

from ddtrace.propagation._database_monitoring import default_sql_injector
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
    from ddtrace.propagation import _database_monitoring
    from ddtrace.trace import tracer

    with tracer.trace("dbspan", service="orders-db") as dbspan:
        # when dbm propagation mode is disabled sqlcomments should NOT be generated
        dbm_popagator = _database_monitoring._DBM_Propagator(0, "query")
        sqlcomment = dbm_popagator._get_dbm_comment(dbspan)
        assert sqlcomment is None

        # when dbm propagation mode is disabled dbm_popagator.inject should NOT add dbm tags to args/kwargs
        args, kwargs = ("SELECT * from table;",), {}
        new_args, new_kwargs = dbm_popagator.inject(dbspan, args, kwargs)
        assert new_kwargs == kwargs
        assert new_args == args

        # when dbm propagation is disabled dbm tags should not be set
        assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) is None


@pytest.mark.subprocess(
    env=dict(
        DD_DBM_PROPAGATION_MODE="service",
        DD_SERVICE="orders-app",
        DD_ENV="staging",
        DD_VERSION="v7343437-d7ac743",
    )
)
def test_dbm_propagation_service_mode():
    from ddtrace.propagation import _database_monitoring
    from ddtrace.trace import tracer

    with tracer.trace("dbspan", service="orders-db") as dbspan:
        # when dbm propagation is service mode sql comments should be generated with dbm tags
        dbm_popagator = _database_monitoring._DBM_Propagator(0, "query")
        sqlcomment = dbm_popagator._get_dbm_comment(dbspan)
        assert sqlcomment == "/*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743'*/ "

        # when dbm propagation mode is service dbm_popagator.inject SHOULD add dbm tags to args/kwargs
        args, kwargs = ("SELECT * from table;",), {}
        new_args, new_kwargs = dbm_popagator.inject(dbspan, args, kwargs.copy())
        assert new_kwargs == {}
        assert new_args == (sqlcomment + args[0],)

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
def test_dbm_propagation_full_mode():
    from ddtrace.propagation import _database_monitoring
    from ddtrace.trace import tracer

    with tracer.trace("dbspan", service="orders-db") as dbspan:
        # since inject() below will call the sampler we just call the sampler here
        # so sampling priority will align in the traceparent
        tracer.sample(dbspan._local_root)

        # when dbm propagation mode is full sql comments should be generated with dbm tags and traceparent keys
        dbm_popagator = _database_monitoring._DBM_Propagator(0, "procedure")
        sqlcomment = dbm_popagator._get_dbm_comment(dbspan)
        # assert tags sqlcomment contains the correct value
        assert (
            sqlcomment
            == "/*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743',traceparent='%s'*/ "
            % (dbspan.context._traceparent,)
        )

        # when dbm propagation mode is full dbm_popagator.inject SHOULD add dbm tags and traceparent to args/kwargs
        args, kwargs = tuple(), {"procedure": "SELECT * from table;"}
        new_args, new_kwargs = dbm_popagator.inject(dbspan, args, kwargs.copy())
        # assert that keyword was updated
        assert new_kwargs == {"procedure": sqlcomment + kwargs["procedure"]}
        # assert that args remain unchanged
        assert new_args == args

        # ensure that dbm tag is set (required for full mode)
        assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) == "true"


@pytest.mark.subprocess(
    env=dict(
        DD_DBM_PROPAGATION_MODE="full",
        DD_SERVICE="orders-app",
        DD_ENV="staging",
        DD_VERSION="v7343437-d7ac743",
        DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED="True",
    )
)
def test_dbm_dddbs_peer_service_enabled():
    from ddtrace.propagation import _database_monitoring
    from ddtrace.trace import tracer

    with tracer.trace("dbname") as dbspan_no_service:
        # when dbm propagation mode is full sql comments should be generated with dbm tags and traceparent keys
        dbm_popagator = _database_monitoring._DBM_Propagator(0, "procedure")
        sqlcomment = dbm_popagator._get_dbm_comment(dbspan_no_service)
        # assert tags sqlcomment contains the correct value
        assert (
            sqlcomment
            == "/*dddbs='orders-app',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743',traceparent='%s'*/ "
            % (dbspan_no_service.context._traceparent,)
        ), sqlcomment

        with tracer.trace("dbname") as dbspan_with_peer_service:
            dbspan_with_peer_service.set_tag_str("db.name", "db-name-test")

            # when dbm propagation mode is full sql comments should be generated with dbm tags and traceparent keys
            dbm_popagator = _database_monitoring._DBM_Propagator(0, "procedure")
            sqlcomment = dbm_popagator._get_dbm_comment(dbspan_with_peer_service)
            # assert tags sqlcomment contains the correct value
            assert (
                sqlcomment == "/*dddb='db-name-test',dddbs='db-name-test',dde='staging',ddps='orders-app',"
                "ddpv='v7343437-d7ac743',traceparent='%s'*/ " % (dbspan_with_peer_service.context._traceparent,)
            ), sqlcomment


@pytest.mark.subprocess(
    env=dict(
        DD_DBM_PROPAGATION_MODE="full",
        DD_SERVICE="orders-app",
        DD_ENV="staging",
        DD_VERSION="v7343437-d7ac743",
    )
)
def test_dbm_peer_entity_tags():
    from ddtrace.propagation import _database_monitoring
    from ddtrace.trace import tracer

    with tracer.trace("dbname") as dbspan:
        dbspan.set_tag("out.host", "some-hostname")
        dbspan.set_tag("db.name", "some-db")

        # since inject() below will call the sampler we just call the sampler here
        # so sampling priority will align in the traceparent
        tracer.sample(dbspan._local_root)

        # when dbm propagation mode is full sql comments should be generated with dbm tags and traceparent keys
        dbm_propagator = _database_monitoring._DBM_Propagator(0, "procedure")
        sqlcomment = dbm_propagator._get_dbm_comment(dbspan)
        # assert tags sqlcomment contains the correct value
        assert (
            sqlcomment == "/*dddb='some-db',dddbs='orders-app',dde='staging',ddh='some-hostname',"
            "ddps='orders-app',ddpv='v7343437-d7ac743',traceparent='%s'*/ " % (dbspan.context._traceparent,)
        ), sqlcomment

        # when dbm propagation mode is service dbm_popagator.inject SHOULD add dbm tags to args/kwargs
        args, kwargs = ("SELECT * from table;",), {}
        new_args, new_kwargs = dbm_propagator.inject(dbspan, args, kwargs.copy())
        assert new_kwargs == {}
        assert new_args == (sqlcomment + args[0],)

        # ensure that dbm tag is set (required in full mode)
        assert dbspan.get_tag(_database_monitoring.DBM_TRACE_INJECTED_TAG) is not None


def test_default_sql_injector(caplog):
    # test sql injection with unicode str
    dbm_comment = "/*dddbs='orders-db'*/ "
    str_query = "select * from table;"
    assert default_sql_injector(dbm_comment, str_query) == "/*dddbs='orders-db'*/ select * from table;"

    # test sql injection with uft-8 byte str query
    dbm_comment = "/*dddbs='orders-db'*/ "
    str_query = "select * from table;".encode("utf-8")
    assert default_sql_injector(dbm_comment, str_query) == b"/*dddbs='orders-db'*/ select * from table;"

    # test sql injection with a non supported type
    with caplog.at_level(logging.INFO):
        dbm_comment = "/*dddbs='orders-db'*/ "
        non_string_object = object()
        result = default_sql_injector(dbm_comment, non_string_object)
        assert result == non_string_object

    assert "Linking Database Monitoring profiles to spans is not supported for the following query type:" in caplog.text
