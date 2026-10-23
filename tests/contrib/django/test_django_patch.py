import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.subprocess()
def test_database_import_does_not_import_optional_drivers():
    from importlib import import_module
    from importlib.abc import MetaPathFinder
    import sys

    drivers = {"psycopg", "psycopg2", "MySQLdb", "pymysql", "mysql"}
    assert not any(name.split(".", 1)[0] in drivers for name in sys.modules)
    attempted_imports = []

    class BlockDrivers(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".", 1)[0] in drivers:
                attempted_imports.append(fullname)
                raise AssertionError(f"Django instrumentation imported {fullname}")

    sys.meta_path.insert(0, BlockDrivers())
    import_module("ddtrace.contrib.internal.django.database")

    assert not attempted_imports
    assert not any(name.split(".", 1)[0] in drivers for name in sys.modules)


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_DJANGO_INSTRUMENT_TEMPLATES": "true"})
def test_instrument_templates_patching():
    import django.template.base

    from ddtrace.internal.wrapping import is_wrapped

    assert is_wrapped(django.template.base.Template.render)


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_DJANGO_TRACING_MINIMAL": "false"})
def test_tracing_minimal_patching():
    import django.template.base

    from ddtrace.internal.wrapping import is_wrapped

    assert is_wrapped(django.template.base.Template.render)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings"},
)
def test_setup_does_not_import_root_urlconf():
    """django.setup() must not pull in ROOT_URLCONF.

    Reading resolver.url_patterns imports the URLconf and the entire view import closure behind it. A process that
    never serves a request, such as a Celery or dramatiq worker, would otherwise load all of it for nothing, which
    cost one reporter 154MB of RSS per worker. Management commands are not in scope: Django's own check_url_config
    imports the URLconf whenever system checks run.
    """
    import sys

    import django
    from django.conf import settings

    django.setup()

    assert settings.ROOT_URLCONF not in sys.modules, (
        f"django.setup() imported {settings.ROOT_URLCONF}; endpoint discovery must stay lazy"
    )

    from ddtrace.internal.endpoints import endpoint_collection

    assert not endpoint_collection.endpoints


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings"},
)
def test_wsgi_application_collects_endpoints():
    """Building the WSGI application must collect endpoints without serving a request.

    The counterpart of test_setup_does_not_import_root_urlconf: gunicorn and uwsgi import a module that calls
    get_wsgi_application(), which constructs a BaseHandler. That is the point where a process commits to serving
    HTTP, so paying for the URLconf import there is free -- the first request would have paid for it anyway.
    """
    import sys

    from django.conf import settings
    from django.core.wsgi import get_wsgi_application

    get_wsgi_application()

    assert settings.ROOT_URLCONF in sys.modules

    from ddtrace.internal.endpoints import endpoint_collection

    paths = {e.path for e in endpoint_collection.endpoints}
    assert "path/" in paths, paths
    # "test/" mounted under include("...extra_urls") at the "include/" prefix, pinning the sub-application prefix
    # joining from #17695 at startup rather than only per-request.
    assert "include/test/" in paths, paths

    # Counterpart of test_wsgi_application_collects_endpoints_without_middleware_instrumentation: with the flag at
    # its default, moving the middleware wrapping under a runtime check must not have stopped it happening.
    from ddtrace.internal.wrapping import is_wrapped
    from tests.contrib.django.middleware import ClsMiddleware

    assert is_wrapped(ClsMiddleware.__call__)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DJANGO_SETTINGS_MODULE": "tests.contrib.django.django_app.settings",
        "DD_DJANGO_INSTRUMENT_MIDDLEWARE": "false",
    },
)
def test_wsgi_application_collects_endpoints_without_middleware_instrumentation():
    """Endpoint discovery must not be gated on DD_DJANGO_INSTRUMENT_MIDDLEWARE.

    The walk hangs off BaseHandler.load_middleware, which used to be wrapped only when middleware instrumentation
    was enabled. The middleware assertion keeps the flag itself honest: without it this test would still pass if
    wrapping had accidentally stayed on.
    """
    from django.core.wsgi import get_wsgi_application

    get_wsgi_application()

    from ddtrace.internal.endpoints import endpoint_collection

    assert endpoint_collection.endpoints

    from ddtrace.internal.wrapping import is_wrapped
    from tests.contrib.django.middleware import ClsMiddleware

    assert not is_wrapped(ClsMiddleware.__call__)


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestDjangoPatch(python_process, scenario):
    module = "tests.contrib.django"
    module += "._test_django_patch_scenarios"
    case = "TestDjangoPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
