import os

import django
import pytest

from tests.utils import snapshot


pytestmark = pytest.mark.skipif("TEST_DATADOG_DJANGO_MIGRATION" in os.environ, reason="test only without migration")


@pytest.mark.skipif(django.VERSION < (2, 0), reason="")
@snapshot(variants={"21x": (2, 0) <= django.VERSION < (2, 2), "": django.VERSION >= (2, 2)})
def test_urlpatterns_include(client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert client.get("/include/test/").status_code == 200


@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_callable_view(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_middleware_trace_partial_based_view(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.django_db
@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_safe_string_encoding(client):
    assert client.get("/safe-template/").status_code == 200


@snapshot(
    variants={
        "18x": django.VERSION < (1, 9),
        "111x": (1, 9) <= django.VERSION < (1, 12),
        "21x": (1, 12) < django.VERSION < (2, 2),
        "": django.VERSION >= (2, 2),
    }
)
def test_404_exceptions(client):
    assert client.get("/404-view/").status_code == 404


@pytest.fixture()
def psycopg2_patched(transactional_db):
    from django.db import connections

    from ddtrace.contrib.psycopg.patch import patch
    from ddtrace.contrib.psycopg.patch import unpatch

    patch()

    # # force recreate connection to ensure psycopg2 patching has occurred
    del connections["postgres"]
    connections["postgres"].close()
    connections["postgres"].connect()

    yield

    unpatch()


@snapshot(ignores=["meta.out.host"])
@pytest.mark.django_db
def test_psycopg_query_default(client, psycopg2_patched):
    """Execute a psycopg2 query on a Django database wrapper"""
    from django.db import connections
    from psycopg2.sql import SQL

    query = SQL("""select 'one' as x""")
    conn = connections["postgres"]
    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"
