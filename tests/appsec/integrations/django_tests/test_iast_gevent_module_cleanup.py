import pytest


@pytest.mark.xfail(
    reason="FIXME: gevent module cleanup breaks pkgutil discovery after a clean Python startup",
)
@pytest.mark.subprocess(env=dict(DD_IAST_ENABLED="true", DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_gevent_cleanup_preserves_pkgutil_discovery():
    import ddtrace.auto  # noqa: F401, I001
    from pathlib import Path
    import pkgutil
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "visible_module.py").touch()
        assert [module.name for module in pkgutil.iter_modules([directory])] == ["visible_module"]


@pytest.mark.xfail(
    reason="FIXME: gevent module cleanup prevents Django from discovering management commands",
)
@pytest.mark.subprocess(env=dict(DD_IAST_ENABLED="true", DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_ddtrace_auto_preserves_django_command_discovery():
    import ddtrace.auto  # noqa: F401, I001
    from django.core.management import get_commands  # noqa: I001

    assert "runserver" in get_commands()
