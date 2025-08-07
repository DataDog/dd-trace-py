from types import FunctionType
from types import ModuleType
from types import TracebackType
import typing
from typing import Any
from typing import Dict
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


class DjangoTemplateWrappingContext(core.DynamicExecutionContextWrapper):
    """
    A context for wrapping django.template.base:Template.render method.
    """

    _name: str = "django.template.render"

    def __init__(self, f: FunctionType, django: ModuleType) -> None:
        super().__init__(f)
        self._django = django

    @classmethod
    def maybe_wrap(cls, f: FunctionType, django: ModuleType) -> None:
        """
        Wrap the django template render method if the configuration allows it.
        """
        if not cls.is_wrapped(f):
            return cls(f, django).wrap()

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

        cls.maybe_wrap(typing.cast(FunctionType, Template.render), django)

    @classmethod
    def uninstrument_module(cls, django_template_base: ModuleType) -> None:
        # Unwrap the render method of the Template class
        Template = getattr(django_template_base, "Template", None)
        if not Template or not hasattr(Template, "render"):
            return

        cls.maybe_unwrap(typing.cast(FunctionType, Template.render))

    def _context_kwargs(self) -> Dict[str, Any]:
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

        return {
            "span_name": "django.template.render",
            "resource": resource,
            "span_type": http.TEMPLATE,
            "tags": tags,
            "tracer": tracer,
        }

    def _should_create_context(self) -> bool:
        return config_django.instrument_templates
