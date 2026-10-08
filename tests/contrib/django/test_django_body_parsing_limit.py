"""Regression tests for DD_APPSEC_BODY_PARSING_SIZE_LIMIT in the Django AppSec body collection (SEC-002).

The request-body collection must never buffer more than the configured limit, and
an over-limit body must not touch ``request.body`` at all.
"""

from ddtrace.contrib.internal.django.utils import _extract_body
from tests.utils import override_global_config


ASM_ON = dict(_asm_enabled=True)


class _NoTouchBodyRequest:
    """Request whose body access must never happen when over the limit."""

    method = "POST"
    content_type = "application/json"
    META = {"CONTENT_LENGTH": "1000000"}

    @property
    def body(self):
        raise AssertionError("request.body must not be read for over-limit bodies")


class _OkRequest:
    method = "POST"
    content_type = "application/json"
    META = {"CONTENT_LENGTH": "9"}

    @property
    def body(self):
        return b'{"value": 1}'


def test_body_over_limit_is_not_read():
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        assert _extract_body(_NoTouchBodyRequest()) is None


def test_body_within_limit_is_collected():
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        assert _extract_body(_OkRequest()) == {"value": 1}


def test_missing_content_length_is_not_collected():
    class _NoLengthRequest(_OkRequest):
        META = {}

    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        assert _extract_body(_NoLengthRequest()) is None


def test_limit_zero_disables_collection():
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=0)):
        assert _extract_body(_OkRequest()) is None
