from types import FunctionType
from types import ModuleType
import typing
from typing import Any
from typing import Dict
from typing import Tuple
from typing import Type
from typing import TypeVar

from ddtrace import config
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.importlib import func_name
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.settings.integration import IntegrationConfig


T = TypeVar("T")

log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = typing.cast(IntegrationConfig, config.django)


def instrument_module(django_template_base: ModuleType) -> None:
    """
    Instrument the django template base module to wrap the render method.
    """
    if not config_django.instrument_templates:
        return

    # Wrap the render method of the Template class
    Template = getattr(django_template_base, "Template", None)
    if not Template or not hasattr(Template, "render"):
        return

    if not is_wrapped_with(Template.render, traced_render):
        wrap(Template.render, traced_render)


def uninstrument_module(django_template_base: ModuleType) -> None:
    # Unwrap the render method of the Template class
    Template = getattr(django_template_base, "Template", None)
    if not Template or not hasattr(Template, "render"):
        return

    unwrap(Template.render, traced_render)


def traced_render(func: FunctionType, args: Tuple[Any], kwargs: Dict[str, Any]) -> Any:
    if not config_django.instrument_templates:
        return func(*args, **kwargs)

    # Get the template instance (self parameter of the render method)
    # Note: instance is a django.template.base.Template
    instance: Type[Any] = args[0]

    # Extract template name
    template_name = maybe_stringify(getattr(instance, "name", None))
    if template_name:
        resource = template_name
    else:
        resource = f"{func_name(instance)}.render"

    # Build tags
    tags = {COMPONENT: config_django.integration_name}
    if template_name:
        tags["django.template.name"] = template_name

    engine = getattr(instance, "engine", None)
    if engine:
        tags["django.template.engine.class"] = func_name(engine)

    with core.context_with_data(
        "django.template.render",
        span_name="django.template.render",
        resource=resource,
        span_type=http.TEMPLATE,
        tags=tags,
        # TODO: Migrate all tests to snapshot tests and remove this
        tracer=config_django._tracer,
    ):
        return func(*args, **kwargs)
