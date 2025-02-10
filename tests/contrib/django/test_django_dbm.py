from django.db import connections
import mock

from ddtrace.trace import Pin
from tests.contrib import shared_tests
from tests.utils import DummyTracer
from tests.utils import override_config
from tests.utils import override_dbm_config
from tests.utils import override_env
from tests.utils import override_global_config

from ...contrib.config import POSTGRES_CONFIG


def get_cursor(tracer, service=None, propagation_mode="service", tags={}):
    conn = connections["postgres"]
    POSTGRES_CONFIG["db"] = conn.settings_dict["NAME"]

    cursor = conn.cursor()

    pin = Pin.get_from(cursor)
    assert pin is not None

    pin._clone(tracer=tracer, tags={**pin.tags, **tags}).onto(cursor)

    return cursor


def test_django_postgres_dbm_propagation_enabled(tracer, transactional_db):
    with override_dbm_config({"propagation_mode": "full"}):
        tracer = DummyTracer()

        cursor = get_cursor(tracer)
        shared_tests._test_dbm_propagation_enabled(tracer, cursor, "postgres")


def test_django_postgres_dbm_propagation_comment_with_global_service_name_configured(tracer, transactional_db):
    """tests if dbm comment is set in postgres"""
    with override_global_config({"service": "orders-app", "env": "staging", "version": "v7343437-d7ac743"}):
        with override_dbm_config({"propagation_mode": "service"}):
            cursor = get_cursor(tracer)
            cursor.__wrapped__ = mock.Mock()

            shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
                config=POSTGRES_CONFIG, db_system="postgresdb", cursor=cursor, wrapped_instance=cursor.__wrapped__
            )


def test_django_postgres_dbm_propagation_comment_integration_service_name_override(tracer, transactional_db):
    """tests if dbm comment is set in postgres"""
    with override_global_config({"service": "orders-app", "env": "staging", "version": "v7343437-d7ac743"}):
        with override_config("django", {"database_service_name": "service-name-override"}):
            with override_dbm_config({"propagation_mode": "service"}):
                cursor = get_cursor(tracer)
                cursor.__wrapped__ = mock.Mock()

                shared_tests._test_dbm_propagation_comment_integration_service_name_override(
                    config=POSTGRES_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
                )


def test_django_postgres_dbm_propagation_comment_pin_service_name_override(tracer, transactional_db):
    """tests if dbm comment is set in postgres"""
    with override_global_config({"service": "orders-app", "env": "staging", "version": "v7343437-d7ac743"}):
        with override_config("django", {"database_service_name": "service-name-override"}):
            with override_dbm_config({"propagation_mode": "service"}):
                cursor = get_cursor(tracer)
                cursor.__wrapped__ = mock.Mock()

                shared_tests._test_dbm_propagation_comment_pin_service_name_override(
                    config=POSTGRES_CONFIG,
                    cursor=cursor,
                    tracer=tracer,
                    wrapped_instance=cursor.__wrapped__,
                    conn=connections["postgres"],
                )


def test_django_postgres_dbm_propagation_comment_peer_service_enabled(tracer, transactional_db):
    """tests if dbm comment is set in postgres"""
    with override_global_config({"service": "orders-app", "env": "staging", "version": "v7343437-d7ac743"}):
        with override_env({"DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED": "True"}):
            with override_config("django", {"database_service_name": "service-name-override"}):
                with override_dbm_config({"propagation_mode": "service"}):
                    cursor = get_cursor(tracer)
                    cursor.__wrapped__ = mock.Mock()

                    shared_tests._test_dbm_propagation_comment_peer_service_enabled(
                        config=POSTGRES_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
                    )
