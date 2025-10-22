from functools import partial
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.module import ModuleWatchdog


def wrap_wsgi_application(application: t.Callable[[dict, t.Any], t.Any]):
    def wrapped_app(env: dict, start_response: t.Any) -> t.Any:
        # DEV: This is where we set the context information from the propagation
        # headers
        print("Intercepted datadog headers:")
        for h, v in env.items():
            if not h.startswith("HTTP_X_DATADOG"):
                continue
            print(f"- {h}: {v}")

        return application(env, start_response)

    return wrapped_app


def wsgi_patch(module: str) -> t.Callable[[t.Callable[[ModuleType], tuple[str | Path, str]]], None]:
    def _(inferrer: t.Callable[[ModuleType], tuple[str | Path, str]]) -> None:
        @ModuleWatchdog.after_module_imported(module)
        def _(m: ModuleType):
            app_module, app_variable = inferrer(m)
            app_variable = app_variable or "application"

            if isinstance(app_module, str):

                @ModuleWatchdog.after_module_imported(app_module)
                def _(m):
                    setattr(m, app_variable, wrap_wsgi_application(getattr(m, app_variable)))

            elif isinstance(app_module, Path):

                @partial(ModuleWatchdog.register_origin_hook, app_module.resolve())
                def _(m):
                    setattr(m, app_variable, wrap_wsgi_application(getattr(m, app_variable)))

            else:
                msg = "Unable to figure out what to patch"
                raise RuntimeError(msg)

    return _


def install():
    # DEV: For each WSGI server we need to register an import hook on a module
    # that we can use to extract the name/file path of the application module
    # and the optional application variable (defaults to "application").
    @wsgi_patch("gunicorn.config")
    def _(gunicorn_config):
        module, _, variable = gunicorn_config.Config().parser().parse_args().args[0].partition(":")
        return module, variable

    @wsgi_patch("uwsgi")
    def _(uwsgi):
        return (
            uwsgi.opt.get("module", b"").decode() or Path(uwsgi.opt.get("wsgi-file", b"").decode()),
            uwsgi.opt.get("callable", b"application").decode(),
        )
