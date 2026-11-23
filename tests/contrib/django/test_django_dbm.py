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
    from tests.contrib import shared_tests
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.contrib.django.utils import setup_django
    from tests.utils import scoped_tracer

    setup_django()
    instrument_dbs(django)

    with scoped_tracer() as tracer:
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

    from unittest import mock

    import django

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import scoped_tracer

    instrument_dbs(django)

    with scoped_tracer():
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

    from unittest import mock

    import django

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import scoped_tracer

    instrument_dbs(django)

    with scoped_tracer():
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

    from unittest import mock

    import django

    from ddtrace.contrib.internal.django.database import instrument_dbs
    from tests.contrib import shared_tests
    from tests.contrib.config import POSTGRES_CONFIG
    from tests.contrib.django.test_django_dbm import get_cursor
    from tests.utils import scoped_tracer

    instrument_dbs(django)

    with scoped_tracer():
        cursor = get_cursor()
        cursor.__wrapped__ = mock.Mock()
        shared_tests._test_dbm_propagation_comment_peer_service_enabled(
            config=POSTGRES_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )


@pytest.mark.subprocess(env={"DD_DBM_PROPAGATION_MODE": "full"})
def test_cached_database_config_supports_late_dbm_attachment():
    from importlib import import_module
    from unittest.mock import Mock

    from ddtrace.contrib.dbapi import TracedCursor

    # Initialize integration defaults without attaching its optional consumers.
    import_module("ddtrace.contrib.internal.django.patch")
    from ddtrace.contrib.internal.django.database import get_conn_config
    from ddtrace.propagation import _database_monitoring as dbm

    cfg = get_conn_config("postgresql", "boundary-cache")
    raw = Mock(rowcount=1)
    cursor = TracedCursor(raw, cfg=cfg)
    cursor.execute("SELECT 1")
    raw.execute.assert_called_with("SELECT 1")
    dbm.django_hooks.listen()
    assert get_conn_config("postgresql", "boundary-cache") is cfg
    cursor.execute("SELECT 1")
    assert raw.execute.call_args.args[0].endswith("*/ SELECT 1")
    dbm.django_hooks.unlisten()
    cursor.execute("SELECT 1")
    raw.execute.assert_called_with("SELECT 1")


@pytest.mark.subprocess(env={"DD_DBM_PROPAGATION_MODE": "full"})
def test_dbm_stays_attached_until_both_psycopg_drivers_are_unpatched():
    from unittest.mock import Mock

    import psycopg
    import psycopg2

    from ddtrace import config
    from ddtrace.contrib.dbapi import TracedCursor
    from ddtrace.contrib.internal.psycopg.patch import _patch
    from ddtrace.contrib.internal.psycopg.patch import _unpatch

    _patch(psycopg)
    _patch(psycopg2)
    raw = Mock(rowcount=1)
    cursor = TracedCursor(raw, cfg=config.psycopg)
    from psycopg.sql import SQL

    query = SQL("SELECT 1")
    _unpatch(psycopg)
    cursor.execute(query)
    rewritten = raw.execute.call_args.args[0]
    assert isinstance(rewritten, psycopg.sql.Composed)
    assert rewritten.as_string().endswith("*/ SELECT 1")
    cursor.execute("SELECT 1")
    assert raw.execute.call_args.args[0].endswith("*/ SELECT 1")
    assert config.psycopg["_patched_modules"] == {psycopg2}
    _unpatch(psycopg2)
    cursor.execute("SELECT 1")
    raw.execute.assert_called_with("SELECT 1")
    assert config.psycopg["_patched_modules"] == set()
