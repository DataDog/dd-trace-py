# -*- coding: utf-8 -*-

import contextlib
import re

import pytest

from ddtrace import config
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import LOGIN_EVENTS_MODE
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._processor import AppSecSpanProcessor  # noqa: F401
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import constants
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry.constants import TELEMETRY_EVENT_TYPE
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.appsec.integrations.django_tests.utils import _aux_appsec_get_root_span
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
        body = result.content.decode()
        body_parsed = re.sub(
            r'"security_response_id":"[-0-9a-z]+"', r'"security_response_id":"[security_response_id]"', body
        )
        assert body_parsed == constants.BLOCKED_RESPONSE_JSON
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
        body = result.content.decode()
        body_parsed = re.sub(
            r'"security_response_id":"[-0-9a-z]+"', r'"security_response_id":"[security_response_id]"', body
        )
        assert body_parsed == constants.BLOCKED_RESPONSE_JSON, (body_parsed, constants.BLOCKED_RESPONSE_JSON)
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/checkuser/%s/" % _BLOCKED_USER
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "application/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "application/json"


def _flush_user_auth_metrics(telemetry_writer):
    metrics = (
        telemetry_writer._namespace.flush()
        .get(TELEMETRY_EVENT_TYPE.METRICS, {})
        .get(TELEMETRY_NAMESPACE.APPSEC.value, [])
    )
    return [m for m in metrics if m["metric"].startswith("instrum.user_auth.")]


def _assert_user_auth_metrics(telemetry_writer, event_type, expected_metric_names):
    metrics = _flush_user_auth_metrics(telemetry_writer)
    assert {m["metric"] for m in metrics} == expected_metric_names
    assert len(metrics) == len(expected_metric_names), metrics
    for metric in metrics:
        assert "framework:django" in metric["tags"]
        assert f"event_type:{event_type}" in metric["tags"]
        assert len(metric["tags"]) == 2


@pytest.mark.django_db
def test_django_user_auth_missing_telemetry_real_flows(client, telemetry_writer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    User.objects.create_user(username="fred", password="secret")

    telemetry_writer._namespace.flush()
    with (
        override_global_config(
            dict(
                _asm_enabled=True,
                _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT,
                # Default Django User exposes `pk`/`id` and `get_username()`,
                # so absent field names would fall back to real identity data. To exercise
                # missing data without mocks or a custom user model, point ddtrace at
                # real fields that are blank by default. Empty strings short-circuit those
                # fallbacks and are treated as missing identity values.
                _user_model_login_field="first_name",
                _user_model_name_field="last_name",
            )
        ),
        update_django_config(),
    ):
        assert not client.login(username="fred", password="wrong")
        _assert_user_auth_metrics(
            telemetry_writer,
            "login_failure",
            {"instrum.user_auth.missing_user_login"},
        )

        response = client.get("/appsec/signup/?login=john&pwd=secret")
        assert response.status_code == 200
        _assert_user_auth_metrics(
            telemetry_writer,
            "signup",
            {"instrum.user_auth.missing_user_login", "instrum.user_auth.missing_user_id"},
        )

        assert client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        _assert_user_auth_metrics(
            telemetry_writer,
            "login_success",
            {"instrum.user_auth.missing_user_login", "instrum.user_auth.missing_user_id"},
        )

        response = client.get("/")
        assert response.status_code == 200
        _assert_user_auth_metrics(
            telemetry_writer,
            "authenticated_request",
            {"instrum.user_auth.missing_user_id"},
        )


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

    with (
        override_global_config(
            dict(
                _asm_enabled=True,
                _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.IDENT,
                _django_include_user_email=use_email,
                _django_include_user_login=use_login,
                _django_include_user_realname=use_realname,
            )
        ),
        update_django_config(),
    ):
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
@pytest.mark.parametrize("mode", (LOGIN_EVENTS_MODE.IDENT, LOGIN_EVENTS_MODE.ANON))
def test_django_authenticated_request_tags_session_id(client, test_spans, tracer, mode):
    """
    Per-request auto-user path must tag both `usr.id` and `usr.session_id` on the entry span
    of authenticated follow-up requests, not only on the `django.contrib.auth.login` event span.
    """
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with (
        override_global_config(
            dict(
                _asm_enabled=True,
                _auto_user_instrumentation_local_mode=mode,
            )
        ),
        update_django_config(),
    ):
        test_user = User.objects.create(username="fred", first_name="Fred", email="fred@test.com")
        test_user.set_password("secret")
        test_user.save()
        assert client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        session_key = client.session.session_key
        assert session_key

        test_spans.reset()
        response = client.get("/")
        assert response.status_code == 200

        request_span = test_spans.find_span(name="django.request")
        assert request_span.get_tag(user.SESSION_ID) == session_key
        if mode == LOGIN_EVENTS_MODE.IDENT:
            assert request_span.get_tag(user.ID)


@pytest.mark.django_db
@pytest.mark.parametrize("use_login", (False, True))
@pytest.mark.parametrize("use_email", (False, True))
@pytest.mark.parametrize("use_realname", (False, True))
def test_django_login_sucess_anonymization(client, test_spans, tracer, use_login, use_email, use_realname):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with (
        override_global_config(
            dict(
                _asm_enabled=True,
                _auto_user_instrumentation_local_mode=LOGIN_EVENTS_MODE.ANON,
                _django_include_user_email=use_email,
                _django_include_user_login=use_login,
                _django_include_user_realname=use_realname,
            )
        ),
        update_django_config(),
    ):
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
