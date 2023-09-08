from django.test.client import RequestFactory
import pytest

from ddtrace.contrib.django.utils import DJANGO22
from ddtrace.contrib.django.utils import _get_request_headers


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
