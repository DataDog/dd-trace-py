from inspect import isclass
from inspect import isfunction
from types import FunctionType
from types import ModuleType
import typing
from typing import Dict
from typing import List
from typing import Optional
from typing import TypeVar

from ddtrace import config
from ddtrace.contrib.internal.django.utils import DjangoFunctionWrappingContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.settings.integration import IntegrationConfig


T = TypeVar("T")

log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = typing.cast(IntegrationConfig, config.django)


class LoadMiddlewareWrappingContext(WrappingContext):
    def __init__(self, f: FunctionType, django: ModuleType) -> None:
        super().__init__(f)
        self._django: ModuleType = django

    @classmethod
    def instrument_module(cls, django_middleware: ModuleType) -> None:
        """
        Instrument the django middleware module to wrap the load_middleware method.
        """
        if not hasattr(django_middleware, "load_middleware"):
            return

        import django

        cls(typing.cast(FunctionType, django_middleware.load_middleware), django).wrap()

    @classmethod
    def uninstrument_module(cls, django_middleware: ModuleType) -> None:
        """
        Unwrap the load_middleware method of the django middleware module.
        """
        if not hasattr(django_middleware, "load_middleware"):
            return

        if cls.is_wrapped(typing.cast(FunctionType, django_middleware.load_middleware)):
            ctx = cls.extract(typing.cast(FunctionType, django_middleware.load_middleware))
            ctx.unwrap()

    def __enter__(self) -> "LoadMiddlewareWrappingContext":
        super().__enter__()

        settings_middleware: List[str] = []
        # Gather all the middleware
        if getattr(self._django.conf.settings, "MIDDLEWARE", None):
            settings_middleware += typing.cast(List[str], self._django.conf.settings.MIDDLEWARE)
        if getattr(self._django.conf.settings, "MIDDLEWARE_CLASSES", None):
            settings_middleware += typing.cast(List[str], self._django.conf.settings.MIDDLEWARE_CLASSES)

        # Iterate over each middleware provided in settings.py
        # Each middleware can either be a function or a class
        for mw_path in settings_middleware:
            mw = self._django.utils.module_loading.import_string(mw_path)

            # Instrument function-based middleware
            if isfunction(mw):
                # Function based middleware is a factory which returns a function
                DjangoMiddewareFactoryWrappingContext.maybe_wrap(
                    mw,
                    self._django,
                    name="django.middleware",
                    resource=mw_path,
                )

            # Instrument class-based middleware
            elif isclass(mw):
                for hook in [
                    "process_request",
                    "process_response",
                    "process_view",
                    "process_template_response",
                    "__call__",
                ]:
                    if hasattr(mw, hook):
                        fn = typing.cast(FunctionType, getattr(mw, hook))
                        DjangoMiddlewareWrappingContext.maybe_wrap(
                            fn,
                            self._django,
                            hook=hook,
                        )

                if hasattr(mw, "process_exception"):
                    fn = typing.cast(FunctionType, getattr(mw, "process_exception"))
                    DjangoProcessExceptionWrappingContext.maybe_wrap(
                        fn,
                        self._django,
                    )

        return self


class DjangoMiddewareFactoryWrappingContext(WrappingContext):
    def __init__(self, f: FunctionType, django: ModuleType, resource: Optional[str] = None) -> None:
        super().__init__(f)
        self._django: ModuleType = django
        self._resource: Optional[str] = resource

    @classmethod
    def maybe_wrap(cls, f: FunctionType, django: ModuleType, name: str, resource: Optional[str] = None) -> None:
        """
        Wrap the function if it is not already wrapped.
        """
        if not cls.is_wrapped(f):
            cls(f, django=django, resource=resource).wrap()

    def __return__(self, value: T) -> T:
        """
        Return the value from the wrapped function.
        """
        value = super().__return__(value)

        if isfunction(value):
            DjangoFunctionWrappingContext.maybe_wrap(
                value, self._django, name="django.middleware", resource=self._resource
            )
        return value


class DjangoMiddlewareWrappingContext(DjangoFunctionWrappingContext):
    def __init__(self, f: FunctionType, django: ModuleType, hook: str) -> None:
        super().__init__(f, django=django, name="django.middleware", resource=None)
        self._hook = hook

    @classmethod
    def maybe_wrap(cls, f: FunctionType, django: ModuleType, hook: str) -> None:
        """
        Wrap the function if it is not already wrapped.
        """
        if not cls.is_wrapped(f):
            cls(f, django=django, hook=hook).wrap()

    def _context_kwargs(self) -> Dict[str, str]:
        """
        Enter the context and set the resource name.
        """
        ctx_kwargs = {}
        try:
            local_self = self.get_local("self")
            mw_path = func_name(local_self)
            resource = f"{mw_path}.{self._hook}"
            ctx_kwargs["resource"] = resource
        except Exception as e:
            log.debug("Could not determine resource name for Django middleware: %s", e)

        return ctx_kwargs


class DjangoProcessExceptionWrappingContext(DjangoMiddlewareWrappingContext):
    def __init__(self, f: FunctionType, django: ModuleType) -> None:
        super().__init__(f, django=django, hook="process_exception")
        self._name = "django.process_exception"

    @classmethod
    def maybe_wrap(cls, f: FunctionType, django: ModuleType) -> None:
        """
        Wrap the function if it is not already wrapped.
        """
        if not cls.is_wrapped(f):
            cls(f, django=django).wrap()

    def __return__(self, value: T) -> T:
        try:
            ctx = self.get("ctx")
            should_set_traceback = hasattr(value, "status_code") and 500 <= value.status_code < 600
            if ctx.span and should_set_traceback:
                ctx.span.set_traceback()
        except Exception as e:
            log.debug("Could not dispatch django.process_exception: %s", e)
        return super().__return__(value)
