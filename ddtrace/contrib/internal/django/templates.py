from types import FunctionType
from types import ModuleType
from types import TracebackType
import typing
from typing import Optional
from typing import Type
from typing import TypeVar

import ddtrace
from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.settings.integration import IntegrationConfig


T = TypeVar("T")

log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = typing.cast(IntegrationConfig, config.django)


class DjangoTemplateWrappingContext(WrappingContext):
    """
    A context for wrapping django.template.base:Template.render method.
    """

    def __init__(self, f: FunctionType, django: ModuleType) -> None:
        super().__init__(f)
        self._django = django

    @classmethod
    def instrument_module(cls, django_template_base: ModuleType) -> None:
        """
        Instrument the django template base module to wrap the render method.
        """
        if not config_django.instrument_templates:
            return

        # Wrap the render method of the Template class
        Template = getattr(django_template_base, "Template", None)
        if not Template or not hasattr(Template, "render"):
            return

        import django

        cls(typing.cast(FunctionType, Template.render), django).wrap()

    @classmethod
    def uninstrument_module(cls, django_template_base: ModuleType) -> None:
        # Unwrap the render method of the Template class
        Template = getattr(django_template_base, "Template", None)
        if not Template or not hasattr(Template, "render"):
            return

        if cls.is_wrapped(Template.render):
            ctx = cls.extract(Template.render)
            ctx.unwrap()

    def __enter__(self) -> "DjangoTemplateWrappingContext":
        super().__enter__()

        if not config_django.instrument_templates:
            return self

        # Get the template instance (self parameter of the render method)
        # Note: instance is a django.template.base.Template
        instance = self.get_local("self")

        # Extract template name
        template_name = maybe_stringify(getattr(instance, "name", None))
        if template_name:
            resource = template_name
        else:
            resource = "{0}.{1}".format(func_name(instance), self.__wrapped__.__name__)

        # Build tags
        tags = {COMPONENT: config_django.integration_name}
        if template_name:
            tags["django.template.name"] = template_name

        engine = getattr(instance, "engine", None)
        if engine:
            tags["django.template.engine.class"] = func_name(engine)

        # Create the span context
        pin = Pin.get_from(self._django)
        tracer = ddtrace.tracer
        if pin:
            tracer = pin.tracer or tracer
        ctx = core.context_with_data(
            "django.template.render",
            span_name="django.template.render",
            resource=resource,
            span_type=http.TEMPLATE,
            tags=tags,
            tracer=tracer,
        )

        # Enter the context and store it
        ctx.__enter__()
        self.set("ctx", ctx)

        return self

    def _close_ctx(
        self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]
    ) -> None:
        try:
            # Close the context and any open span
            ctx = self.get("ctx")
            ctx.__exit__(exc_type, exc_val, exc_tb)
        except Exception:
            log.exception("Failed to close Django template render wrapping context")

    def __return__(self, value: T) -> T:
        if config_django.instrument_templates:
            self._close_ctx(None, None, None)
        return super().__return__(value)

    def __exit__(
        self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[TracebackType]
    ) -> None:
        if config_django.instrument_templates:
            self._close_ctx(exc_type, exc_val, exc_tb)
        return super().__exit__(exc_type, exc_val, exc_tb)
