from django.db import connections
import pytest

from tests.contrib.config import POSTGRES_CONFIG


def get_cursor():
    conn = connections["postgres"]
    POSTGRES_CONFIG["db"] = conn.settings_dict["NAME"]

    return conn.cursor()


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_DJANGO_INSTRUMENT_DATABASES": "true",
        "DD_DBM_PROPAGATION_MODE": "full",
        "DD_TRACE_PSYCOPG_ENABLED": "false",
    },
)
def test_django_postgres_dbm_propagation_enabled():
    import django

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from ddtrace.internal.settings._config import config
    from tests.contrib import shared_tests
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import DummyTracer

    instrument_dbs(django)

    tracer = DummyTracer()
    config.django._tracer = tracer

    cursor = get_cursor()

    shared_tests._test_dbm_propagation_enabled(tracer, cursor, "postgres")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_DJANGO_INSTRUMENT_DATABASES": "true",
        "DD_DBM_PROPAGATION_MODE": "service",
        "DD_TRACE_PSYCOPG_ENABLED": "false",
        "DD_SERVICE": "orders-app",
        "DD_ENV": "staging",
        "DD_VERSION": "v7343437-d7ac743",
    },
)
def test_django_postgres_dbm_propagation_comment_with_global_service_name_configured():
    """tests if dbm comment is set in postgres"""

    import django
    import mock

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from ddtrace.internal.settings._config import config
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import DummyTracer

    instrument_dbs(django)

    tracer = DummyTracer()
    config.django._tracer = tracer

    cursor = get_cursor()
    cursor.__wrapped__ = mock.Mock()

    shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
        config=POSTGRES_CONFIG,
        db_system="postgresdb",
        cursor=cursor,
        wrapped_instance=cursor.__wrapped__,
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_DJANGO_INSTRUMENT_DATABASES": "true",
        "DD_DJANGO_DATABASE_SERVICE_NAME": "service-name-override",
        "DD_DBM_PROPAGATION_MODE": "service",
        "DD_TRACE_PSYCOPG_ENABLED": "false",
        "DD_SERVICE": "orders-app",
        "DD_ENV": "staging",
        "DD_VERSION": "v7343437-d7ac743",
    },
)
def test_django_postgres_dbm_propagation_comment_integration_service_name_override():
    """tests if dbm comment is set in postgres"""

    import django
    import mock

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from ddtrace.internal.settings._config import config
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import DummyTracer

    instrument_dbs(django)

    tracer = DummyTracer()
    config.django._tracer = tracer

    cursor = get_cursor()
    cursor.__wrapped__ = mock.Mock()

    shared_tests._test_dbm_propagation_comment_integration_service_name_override(
        config=POSTGRES_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_DJANGO_INSTRUMENT_DATABASES": "true",
        "DD_DJANGO_DATABASE_SERVICE_NAME": "service-name-override",
        "DD_DBM_PROPAGATION_MODE": "service",
        "DD_TRACE_PSYCOPG_ENABLED": "false",
        "DD_SERVICE": "orders-app",
        "DD_ENV": "staging",
        "DD_VERSION": "v7343437-d7ac743",
    },
)
def test_django_postgres_dbm_propagation_comment_pin_service_name_override():
    """tests if dbm comment is set in postgres"""

    import django
    from django.db import connections
    import mock

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from ddtrace.internal.settings._config import config
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import DummyTracer

    instrument_dbs(django)

    tracer = DummyTracer()
    config.django._tracer = tracer

    cursor = get_cursor()
    cursor.__wrapped__ = mock.Mock()

    shared_tests._test_dbm_propagation_comment_pin_service_name_override(
        config=POSTGRES_CONFIG,
        cursor=cursor,
        tracer=tracer,
        wrapped_instance=cursor.__wrapped__,
        conn=connections["postgres"],
    )


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_DJANGO_INSTRUMENT_DATABASES": "true",
        "DD_DJANGO_DATABASE_SERVICE_NAME": "service-name-override",
        "DD_DBM_PROPAGATION_MODE": "service",
        "DD_TRACE_PSYCOPG_ENABLED": "false",
        "DD_SERVICE": "orders-app",
        "DD_ENV": "staging",
        "DD_VERSION": "v7343437-d7ac743",
        "DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED": "true",
    },
)
def test_django_postgres_dbm_propagation_comment_peer_service_enabled():
    """tests if dbm comment is set in postgres"""

    import django
    import mock

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from ddtrace.internal.settings._config import config
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import DummyTracer

    instrument_dbs(django)

    tracer = DummyTracer()
    config.django._tracer = tracer

    cursor = get_cursor()
    cursor.__wrapped__ = mock.Mock()

    shared_tests._test_dbm_propagation_comment_peer_service_enabled(
        config=POSTGRES_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
    )
