import pytest


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="true"),
    parametrize=dict(_DD_TEST_IAST_STARTUP=("auto", "uwsgi", "manual")),
    err=None,
)
def test_iast_instrumentation_listener_lifecycle():
    import os
    from unittest import mock

    from ddtrace.appsec._iast import _listener
    from ddtrace.appsec._iast import load_iast
    from ddtrace.internal import core
    from ddtrace.internal import products
    from ddtrace.internal.iast import product
    from ddtrace.internal.uwsgi import uWSGIMasterProcess

    startup = os.environ["_DD_TEST_IAST_STARTUP"]
    with (
        mock.patch.object(products, "check_uwsgi", side_effect=uWSGIMasterProcess if startup == "uwsgi" else None),
        mock.patch.multiple(
            _listener,
            _on_flask_patch=mock.DEFAULT,
            _on_django_patch=mock.DEFAULT,
            _on_langchain_patch=mock.DEFAULT,
            _on_langchain_unpatch=mock.DEFAULT,
        ) as handlers,
    ):
        if startup == "manual":
            load_iast()
        else:
            import ddtrace.auto  # noqa: F401

        assert core.has_listeners("flask.request_init") is (startup != "uwsgi")

        events = (
            ("flask.patch", ((3, 0, 0),), handlers["_on_flask_patch"]),
            ("django.patch", (), handlers["_on_django_patch"]),
            ("langchain.patch", (), handlers["_on_langchain_patch"]),
            ("langchain.unpatch", (), handlers["_on_langchain_unpatch"]),
        )
        for event, args, handler in events:
            core.dispatch(event, args)
            handler.assert_called_once_with(*args)

        if startup == "manual":
            product.post_preload()
            for event, args, handler in events:
                core.dispatch(event, args)
                assert handler.call_args_list == [mock.call(*args), mock.call(*args)]


@pytest.mark.subprocess(env=dict(DD_IAST_ENABLED="false", DD_APPSEC_ENABLED="false"), err=None)
def test_iast_instrumentation_listeners_disabled():
    import sys

    import ddtrace.auto  # noqa: F401
    from ddtrace.internal import core

    for event in ("sql.patch", "flask.patch", "django.patch", "langchain.patch", "langchain.unpatch"):
        assert not core.has_listeners(event)
    assert not any(name.startswith("ddtrace.appsec._iast") for name in sys.modules)
