from types import SimpleNamespace

from django.test.client import RequestFactory
import pytest

from ddtrace.contrib.internal.django import database
from ddtrace.contrib.internal.django.utils import DJANGO22
from ddtrace.contrib.internal.django.utils import _get_request_headers


@pytest.mark.skipif(DJANGO22, reason="We only parse environ/headers on Django < 2.2.0")
@pytest.mark.parametrize(
    "meta,expected",
    [
        ({}, {}),
        # This is a regression for #6284
        # DEV: We were checking for `HTTP` prefix for headers instead of `HTTP_` which is required
        ({"HTTPS": "on"}, {}),
        ({"HTTP_HEADER": "value"}, {"Header": "value"}),
    ],
)
def test_get_request_headers(meta, expected):
    factory = RequestFactory()
    request = factory.get("/")
    request.META = meta

    headers = _get_request_headers(request)
    assert headers == expected


def test_database_connection_metadata_isolation():
    cursors = []
    for alias in ("primary", "replica"):
        connection = SimpleNamespace(
            alias=alias,
            vendor="sqlite",
            settings_dict={"NAME": alias, "HOST": alias, "PORT": "1234", "USER": alias},
        )
        cursor = database.cursor(lambda conn: SimpleNamespace(cursor=object()), (connection,), {})
        cursors.append(cursor)
    for alias, cursor in zip(("primary", "replica"), cursors):
        assert cursor._self_config._default_service == database.get_conn_service_name(alias)
        assert cursor._self_db_tags["django.db.alias"] == alias
        assert cursor._self_db_tags["db.name"] == alias
        assert cursor._self_db_tags["out.host"] == alias
    assert cursors[0]._self_db_tags is not cursors[1]._self_db_tags
