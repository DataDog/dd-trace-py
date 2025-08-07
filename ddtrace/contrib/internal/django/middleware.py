from types import FunctionType, ModuleType
from typing import Any, Dict, cast

import ddtrace
from ddtrace._trace.pin import Pin
from ddtrace.internal.constants import COMPONENT
from ddtrace.settings.integration import IntegrationConfig
from ddtrace.internal.core import DynamicExecutionContextWrapper


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, ddtrace.config.django)


class DjangoMiddlewareWrapperMixin(DynamicExecutionContextWrapper):
    _resource: str

    def __init__(self, f: FunctionType, django: ModuleType, resource: str) -> None:
        super().__init__(f)
        self._django: ModuleType = django
        self._resource: str = resource

    def _context_kwargs(self) -> Dict[str, Any]:
        pin = Pin.get_from(self._django)
        tracer = ddtrace.tracer
        if pin:
            tracer = pin.tracer or tracer

        return {
            "span_name": "django.middleware",
            "resource": self._resource,
            "tags": {
                COMPONENT: config_django.integration_name,
            },
            "tracer": tracer,
        }

    @classmethod
    def maybe_wrap(cls, f: FunctionType, django: ModuleType, resource: str) -> None:
        """
        Wrap the middleware process_request method if the configuration allows it.
        """
        if not cls.is_wrapped(f):
            return cls(f, django, resource).wrap()

    def _should_wrap(self) -> bool:
        # Only wrap if the configuration allows it
        return config_django.instrument_middleware


class DjangoMiddlewareProcessRequestWrapper(DjangoMiddlewareWrapperMixin):
    _name: str = "django.middleware.process_request"


class DjangoMiddlewareProcessResponseWrapper(DjangoMiddlewareWrapperMixin):
    _name: str = "django.middleware.process_response"


class DjangoMiddlewareProcessViewWrapper(DjangoMiddlewareWrapperMixin):
    _name: str = "django.middleware.process_view"


class DjangoMiddlewareProcessTemplateResponseWrapper(DjangoMiddlewareWrapperMixin):
    _name: str = "django.middleware.process_template_response"


class DjangoMiddlewareCallWrapper(DjangoMiddlewareWrapperMixin):
    _name: str = "django.middleware.__call__"


def wrap_middleware_class(mw: type, mw_path: str, django: ModuleType) -> None:
    if hasattr(mw, "process_request"):
        if mw_path == "django.contrib.auth.middleware.AuthenticationMiddleware":
            # TODO: We need special handling for this process_request method
            pass
        else:
            DjangoMiddlewareProcessRequestWrapper.maybe_wrap(
                cast(FunctionType, mw.process_request), django, resource=f"{mw_path}.process_request"
            )

    if hasattr(mw, "process_response"):
        DjangoMiddlewareProcessResponseWrapper.maybe_wrap(
            cast(FunctionType, mw.process_response), django, resource=f"{mw_path}.process_response"
        )

    if hasattr(mw, "process_view"):
        DjangoMiddlewareProcessViewWrapper.maybe_wrap(
            cast(FunctionType, mw.process_view), django, resource=f"{mw_path}.process_view"
        )

    if hasattr(mw, "process_template_response"):
        DjangoMiddlewareProcessTemplateResponseWrapper.maybe_wrap(
            cast(FunctionType, mw.process_template_response),
            django,
            resource=f"{mw_path}.process_template_response",
        )

    if hasattr(mw, "__call__"):
        DjangoMiddlewareCallWrapper.maybe_wrap(cast(FunctionType, mw.__call__), django, resource=f"{mw_path}.__call__")

    # # Do a little extra for `process_exception`
    # if hasattr(mw, "process_exception") and not trace_utils.iswrapped(mw, "process_exception"):
    #     res = mw_path + ".{0}".format("process_exception")
    #     trace_utils.wrap(mw, "process_exception", traced_process_exception(django, "django.middleware", resource=res))
