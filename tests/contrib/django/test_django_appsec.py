# -*- coding: utf-8 -*-

import pytest

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._processor import AppSecSpanProcessor  # noqa: F401
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import constants
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.utils import override_env
from tests.utils import override_global_config


def _aux_appsec_get_root_span(
    client,
    test_spans,
    tracer,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    cookies=None,
):
    if cookies is None:
        cookies = {}
    tracer._asm_enabled = asm_config._asm_enabled
    tracer._iast_enabled = asm_config._iast_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    # Set cookies
    client.cookies.load(cookies)
    if payload is None:
        if headers:
            response = client.get(url, **headers)
        else:
            response = client.get(url)
    else:
        if headers:
            response = client.post(url, payload, content_type=content_type, **headers)
        else:
            response = client.post(url, payload, content_type=content_type)
    return test_spans.spans[0], response


def test_django_client_ip_nothing(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d")
        ip = root_span.get_tag(http.CLIENT_IP)
        assert not ip or ip == "127.0.0.1"  # this varies when running under PyCharm or CI


def test_request_block_request_callable(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/block/",
            headers={"HTTP_X_REAL_IP": rules._IP.DEFAULT, "HTTP_USER_AGENT": "fooagent"},
        )
        # Should not block by IP, but the block callable is called directly inside that view
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8")
        assert result.content == as_bytes
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/block/"
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(http.USER_AGENT) == "fooagent"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/json"


_BLOCKED_USER = "123456"
_ALLOWED_USER = "111111"


def test_request_userblock_200(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _ALLOWED_USER
        )
        assert result.status_code == 200
        assert root.get_tag(http.STATUS_CODE) == "200"


def test_request_userblock_403(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _BLOCKED_USER
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8")
        assert result.content == as_bytes
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/checkuser/%s/" % _BLOCKED_USER
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/json"


@pytest.mark.django_db
def test_django_login_events_disabled_explicitly(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="disabled")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated

        with pytest.raises(AssertionError) as excl_info:
            _ = test_spans.find_span(name="django.contrib.auth.login")
        assert "No span found for filter" in str(excl_info.value)


@pytest.mark.django_db
def test_django_login_events_disabled_noappsec(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=False, _automatic_login_events_mode="safe")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated

        with pytest.raises(AssertionError) as excl_info:
            _ = test_spans.find_span(name="django.contrib.auth.login")
        assert "No span found for filter" in str(excl_info.value)


@pytest.mark.django_db
def test_django_login_sucess_extended(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="extended")):
        test_user = User.objects.create(username="fred", first_name="Fred", email="fred@test.com")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "1"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.track") == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "extended"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") == "fred"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") == "fred@test.com"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") == "Fred"


@pytest.mark.django_db
def test_django_login_sucess_safe(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="safe")):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "1"
        assert login_span.get_tag("appsec.events.users.login.success.track") == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "safe"
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.email")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.username")


@pytest.mark.django_db
def test_django_login_sucess_safe_is_default_if_wrong(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="foobar")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(user.ID) == "1"


@pytest.mark.django_db
def test_django_login_sucess_safe_is_default_if_missing(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True)):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(user.ID) == "1"


@pytest.mark.django_db
def test_django_login_failure_user_doesnt_exists(client, test_spans, tracer):
    from django.contrib.auth import get_user

    with override_global_config(dict(_asm_enabled=True, _automatic_login_events_mode="extended")):
        assert not get_user(client).is_authenticated
        client.login(username="missing", password="secret2")
        assert not get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag("appsec.events.users.login.failure.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "missing"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "false"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == "extended"


@pytest.mark.django_db
def test_django_login_sucess_safe_but_user_set_login(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(_asm_enabled=True, _user_model_login_field="username", _automatic_login_events_mode="safe")
    ):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "fred2"
        assert login_span.get_tag("appsec.events.users.login.success.track") == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == "safe"
