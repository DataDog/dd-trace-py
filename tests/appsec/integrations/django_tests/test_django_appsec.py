# -*- coding: utf-8 -*-

import contextlib

import pytest

from ddtrace import config
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._processor import AppSecSpanProcessor  # noqa: F401
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import constants
from ddtrace.settings.asm import config as asm_config
import tests.appsec.rules as rules
from tests.utils import override_global_config


@contextlib.contextmanager
def update_django_config():
    initial_settings = (
        config.django.include_user_email,
        config.django.include_user_login,
        config.django.include_user_realname,
    )
    config.django.include_user_email = asm_config._django_include_user_email
    config.django.include_user_login = asm_config._django_include_user_login
    config.django.include_user_realname = asm_config._django_include_user_realname
    yield
    (
        config.django.include_user_email,
        config.django.include_user_login,
        config.django.include_user_realname,
    ) = initial_settings


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
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer._recreate()
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
    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
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
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "application/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "application/json"


@pytest.mark.django_db
def test_create_new_user(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        root, result = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/signup/?login=john&pwd=secret",
            headers={"HTTP_USER_AGENT": "fooagent"},
        )
        # Should not block by IP, but the block callable is called directly inside that view
        assert result.status_code == 200
        assert result.content == b"OK"
        assert root.get_tag(http.STATUS_CODE) == "200"
        assert root.get_tag(http.URL) == "http://testserver/appsec/signup/?login=john&<redacted>", root.get_tag(
            http.URL
        )
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(http.USER_AGENT) == "fooagent"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type").startswith("text/html")
        if hasattr(result, "headers"):
            assert result.headers["content-type"].startswith("text/html")
        assert root.get_tag("appsec.events.users.signup.usr.login") == "john"
        assert root.get_tag("_dd.appsec.usr.login") == "john"
        assert root.get_tag("_dd.appsec.events.users.signup.auto.mode") == "identification", root.get_tag(
            "_dd.appsec.events.users.signup.auto.mode"
        )
        assert root.get_tag("appsec.events.users.signup.track") == "true"
        assert root.get_tag("appsec.events.users.signup.usr.id")
        assert root.get_tag("_dd.appsec.usr.id")


_BLOCKED_USER = "123456"
_ALLOWED_USER = "111111"


def test_request_userblock_200(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _ALLOWED_USER
        )
        assert result.status_code == 200
        assert root.get_tag(http.STATUS_CODE) == "200"


def test_request_userblock_403(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _BLOCKED_USER
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8")
        assert result.content == as_bytes
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/checkuser/%s/" % _BLOCKED_USER
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "application/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "application/json"


@pytest.mark.django_db
def test_django_login_events_disabled_explicitly(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.DISABLED)
    ):
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

    with override_global_config(dict(_asm_enabled=False, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON)):
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
@pytest.mark.parametrize("use_login", (False, True))
@pytest.mark.parametrize("use_email", (False, True))
@pytest.mark.parametrize("use_realname", (False, True))
def test_django_login_sucess_identification(client, test_spans, tracer, use_login, use_email, use_realname):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(
            _asm_enabled=True,
            _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT,
            _django_include_user_email=use_email,
            _django_include_user_login=use_login,
            _django_include_user_realname=use_realname,
        )
    ), update_django_config():
        # update django config for tests
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
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.IDENT
        if use_login:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") == "fred"
        else:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") is None
        if use_email:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") == "fred@test.com"
            assert login_span.get_tag("usr.email") == "fred@test.com"
        else:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") is None
            assert login_span.get_tag("usr.email") is None
        if use_realname:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") == "Fred"
            assert login_span.get_tag("usr.name") == "Fred"
        else:
            assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") is None
            assert login_span.get_tag("usr.name") is None


@pytest.mark.django_db
@pytest.mark.parametrize("use_login", (False, True))
@pytest.mark.parametrize("use_email", (False, True))
@pytest.mark.parametrize("use_realname", (False, True))
def test_django_login_sucess_anonymization(client, test_spans, tracer, use_login, use_email, use_realname):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(
            _asm_enabled=True,
            _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON,
            _django_include_user_email=use_email,
            _django_include_user_login=use_login,
            _django_include_user_realname=use_realname,
        )
    ), update_django_config():
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
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.ANON
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") == (
            "anon_d1ad1f735a4381c2e8dbed0222db1136" if use_login else None
        )
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") is None
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") is None


@pytest.mark.django_db
def test_django_login_sucess_disabled(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.DISABLED)
    ):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        with pytest.raises(AssertionError):
            _ = test_spans.find_span(name="django.contrib.auth.login")


@pytest.mark.django_db
def test_django_login_sucess_anonymous_username(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT)):
        test_user = User.objects.create(username="AnonymousUser")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="AnonymousUser", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(user.ID) == "1"


@pytest.mark.django_db
def test_django_login_sucess_ident_is_default_if_missing(client, test_spans, tracer):
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
        assert login_span.get_tag("appsec.events.users.login.success.track") == "true"


@pytest.mark.django_db
def test_django_login_failure_user_doesnt_exists(client, test_spans, tracer):
    from django.contrib.auth import get_user

    with override_global_config(dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT)):
        assert not get_user(client).is_authenticated
        client.login(username="missing", password="secret2")
        assert not get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag("appsec.events.users.login.failure.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "missing"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "false"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.IDENT


@pytest.mark.django_db
def test_django_login_failure_identification_user_does_exist(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT)):
        test_user = User.objects.create(username="fred", first_name="Fred", email="fred@test.com")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="wrong")
        assert not get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag("appsec.events.users.login.failure.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "1"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.IDENT
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.email") is None
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.username") is None


@pytest.mark.django_db
def test_django_login_failure_anonymization_user_does_exist(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_asm_enabled=True, _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON)):
        test_user = User.objects.create(username="fred", first_name="Fred", email="fred@test.com")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="wrong")
        assert not get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag("appsec.events.users.login.failure.track") == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.ANON
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID) == "1"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.EXISTS) == "true"
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.email")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.username")


@pytest.mark.django_db
def test_django_login_sucess_anonymization_but_user_set_login(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(
            _asm_enabled=True,
            _user_model_login_field="username",
            _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON,
        )
    ):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "anon_d1ad1f735a4381c2e8dbed0222db1136"
        assert login_span.get_tag("appsec.events.users.login.success.track") == "true"
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_SUCCESS_MODE) == LOGIN_EVENTS_MODE.ANON
        assert (
            login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login")
            == "anon_d1ad1f735a4381c2e8dbed0222db1136"
        )
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.email")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".success.username")


@pytest.mark.django_db
def test_django_login_failure_anonymization_but_user_set_login(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(
            _asm_enabled=True,
            _user_model_login_field="username",
            _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON,
        )
    ):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="wrong")
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(APPSEC.AUTO_LOGIN_EVENTS_FAILURE_MODE) == LOGIN_EVENTS_MODE.ANON
        assert login_span.get_tag("appsec.events.users.login.failure.track") == "true"
        assert (
            login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure." + user.ID)
            == "anon_d1ad1f735a4381c2e8dbed0222db1136"
        )
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.email")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX_PUBLIC + ".failure.username")
