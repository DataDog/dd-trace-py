# -*- coding: utf-8 -*-
import json

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


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"HTTP_X_CLIENT_IP": "", "HTTP_X_FORWARDED_FOR": "4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
    ],
)
def test_django_client_ip_headers(client, test_spans, tracer, kwargs, expected):
    with override_global_config(dict(_asm_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d", headers=kwargs)
        assert root_span.get_tag(http.CLIENT_IP) == expected


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


def test_request_suspicious_request_block_custom_actions(client, test_spans, tracer):
    import ddtrace.internal.utils.http as http

    # remove cache to avoid using template from other tests
    http._HTML_BLOCKED_TEMPLATE_CACHE = None
    http._JSON_BLOCKED_TEMPLATE_CACHE = None

    # value suspicious_306_auto must be blocked
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_306_auto"
        )
        assert response.status_code == 306
        # check if response content is custom as expected
        assert json.loads(response.content.decode()) == {
            "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
        }
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-001"]
    # value suspicious_306_auto must be blocked with text if required
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_306_auto", headers={"HTTP_ACCEPT": "text/html"}
        )
        assert response.status_code == 306
        # check if response content is custom as expected
        assert b"192837645" in response.content
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-001"]

    # value suspicious_429_json must be blocked
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_429_json"
        )
        assert response.status_code == 429
        # check if response content is custom as expected
        assert json.loads(response.content.decode()) == {
            "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
        }
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-002"]
    # value suspicious_429_json must be blocked with json even if text if required
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_429_json", headers={"HTTP_ACCEPT": "text/html"}
        )
        assert response.status_code == 429
        # check if response content is custom as expected
        assert json.loads(response.content.decode()) == {
            "errors": [{"title": "You've been blocked", "detail": "Custom content"}]
        }
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-002"]

    # value suspicious_503_html must be blocked with text even if json is required
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_503_html", headers={"HTTP_ACCEPT": "text/json"}
        )
        assert response.status_code == 503
        # check if response content is custom as expected
        assert b"192837645" in response.content

        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-003"]
    # value suspicious_503_html must be blocked with text if required
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON=rules.RESPONSE_CUSTOM_JSON,
            DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML=rules.RESPONSE_CUSTOM_HTML,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=suspicious_503_html", headers={"HTTP_ACCEPT": "text/html"}
        )
        assert response.status_code == 503
        # check if response content is custom as expected
        assert b"192837645" in response.content
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-040-003"]

    # other values must not be blocked
    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=ytrace")
        assert response.status_code == 404
    # appsec disabled must not block
    with override_global_config(dict(_asm_enabled=False)), override_env(dict(DD_APPSEC_RULES=rules.RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=suspicious_503_html")
        assert response.status_code == 404
    # remove cache to avoid other tests from using the templates of this test
    http._HTML_BLOCKED_TEMPLATE_CACHE = None
    http._JSON_BLOCKED_TEMPLATE_CACHE = None


@pytest.mark.parametrize(
    ["suspicious_value", "expected_code", "rule"],
    [
        ("suspicious_301", 301, "tst-040-004"),
        ("suspicious_303", 303, "tst-040-005"),
    ],
)
def test_request_suspicious_request_redirect_actions(client, test_spans, tracer, suspicious_value, expected_code, rule):
    # value suspicious_306_auto must be blocked
    with override_global_config(dict(_asm_enabled=True)), override_env(
        dict(
            DD_APPSEC_RULES=rules.RULES_SRBCA,
        )
    ):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="index.html?toto=%s" % suspicious_value
        )
        assert response.status_code == expected_code
        # check if response content is custom as expected
        assert not response.content
        assert response["location"] == "https://www.datadoghq.com"
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == [rule]


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


# nested events
def test_nested_appsec_events(client, test_spans, tracer):
    # activate two monitoring rules, once on request and the other on response.
    # check if they are both triggered and merged correctly
    with override_global_config(dict(_asm_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/config.php", headers={"HTTP_USER_AGENT": "Arachni/v1.5.1"}
        )
        assert response.status_code == 404
        appsec_json = root_span.get_tag(APPSEC.JSON)
        assert appsec_json
        loaded = json.loads(appsec_json)
        assert "triggers" in loaded
        rules = loaded["triggers"]
        assert len(rules) == 2
        assert any(r["rule"]["id"] == "ua0-600-12x" for r in rules)
        assert any(r["rule"]["id"] == "nfd-000-001" for r in rules)
        assert all("span_id" in r for r in rules)
