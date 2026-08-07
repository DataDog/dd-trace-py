import pytest


@pytest.mark.subprocess(
    env={
        "DD_APPSEC_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    }
)
def test_appsec_listeners_follow_activation_lifecycle():
    from unittest import mock

    from ddtrace.appsec._contrib.openai import handlers as openai_handlers
    from ddtrace.appsec._listeners import disable_appsec
    from ddtrace.appsec._listeners import load_appsec
    from ddtrace.contrib._events.http_client import HttpClientEvents
    from ddtrace.internal import core

    appsec_events = (
        "set_http_meta_for_asm",
        "asm.set_blocked",
        "asm.get_blocked",
        "aws_lambda.start_request",
        "django.login",
        "asgi.start_request",
        f"context.started.{HttpClientEvents.HTTPX_SEND_REQUEST.value}",
        "openai.chat.completions.create.before",
        "appsec.stripe.checkout.session.create",
        "tornado.start_request",
        "set_user_for_asm",
        "waf.update",
    )

    # Disabling before the first explicit load must also be safe and complete.
    disable_appsec()
    active_events = [event for event in appsec_events if core.has_listeners(event)]
    if active_events:
        raise AssertionError(active_events)

    with mock.patch.object(openai_handlers, "in_asm_context", return_value=False) as in_asm_context:
        assert load_appsec()
        assert load_appsec()
        assert all(core.has_listeners(event) for event in appsec_events)

        core.dispatch("openai.chat.completions.create.before", ({},))
        in_asm_context.assert_called_once_with()

        core.dispatch("wsgi.block_decided", (lambda: None,))
        assert core.has_listeners("flask.block.request.content")

        disable_appsec()
        disable_appsec()
        assert not any(core.has_listeners(event) for event in appsec_events)
        assert not core.has_listeners("flask.block.request.content")

        core.dispatch("wsgi.block_decided", (lambda: None,))
        assert not core.has_listeners("flask.block.request.content")
        core.dispatch("openai.chat.completions.create.before", ({},))
        in_asm_context.assert_called_once_with()

        assert load_appsec()
        assert all(core.has_listeners(event) for event in appsec_events)
        core.dispatch("openai.chat.completions.create.before", ({},))
        assert in_asm_context.call_count == 2
