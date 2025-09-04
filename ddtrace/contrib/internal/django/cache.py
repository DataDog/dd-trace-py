from collections.abc import Iterable
from types import FunctionType
from types import ModuleType
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Type
from typing import cast

from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import wrap
from ddtrace.settings.integration import IntegrationConfig

from . import utils


log = get_logger(__name__)

# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, config.django)


@cached()
def get_service_name(name: Optional[str]) -> Optional[str]:
    return schematize_service_name(name)


@cached()
def func_cache_operation(func: FunctionType) -> str:
    """
    Returns the cache operation name for the given function.
    This is used to set the resource name for the cache span.
    """
    # Extract ".delete" from "LoMemCache.delete"
    # DEV: We have to use "__qualname__" since `wrap` will overwrite the name with `<wrapped>`
    fname = getattr(func, "__qualname__", func.__name__)
    _, _, fname = fname.rpartition(".")
    return fname


def traced_cache(func: FunctionType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    if not config_django.instrument_caches:
        return func(*args, **kwargs)

    instance = args[0]

    cache_backend = "{}.{}".format(instance.__module__, instance.__class__.__name__)
    tags = {COMPONENT: config_django.integration_name, "django.cache.backend": cache_backend}
    if len(args) > 1:
        keys = utils.quantize_key_values(args[1])
        tags["django.cache.key"] = keys

    cache_operation = func_cache_operation(func)
    resource = f"{func.__module__}.{cache_operation}"

    key_prefix = getattr(instance, "key_prefix", None)
    if key_prefix:
        resource = f"{resource} {key_prefix}"
    resource = resource.lower()

    with core.context_with_data(
        "django.cache",
        span_name="django.cache",
        span_type=SpanTypes.CACHE,
        service=get_service_name(config_django.cache_service_name),
        resource=resource,
        tags=tags,
        # TODO: Migrate all tests to snapshot tests and remove this
        tracer=config_django._tracer,
    ) as ctx:
        result = func(*args, **kwargs)

        rowcount = 0
        if cache_operation == "get_many":
            rowcount = sum(1 for doc in result if doc) if result and isinstance(result, Iterable) else 0
        elif cache_operation == "get":
            try:
                # check also for special case for Django~3.2 that returns an empty Sentinel
                # object for empty results
                # also check if result is Iterable first since some iterables return ambiguous
                # truth results with ``==``
                if result is None or (
                    not isinstance(result, Iterable) and result == getattr(instance, "_missing_key", None)
                ):
                    rowcount = 0
                else:
                    rowcount = 1
            except (AttributeError, NotImplementedError, ValueError):
                pass

        # Accessible from `context.ended.django.cache` event
        ctx.set_item("rowcount", rowcount)
        return result


def instrument_caches(django: ModuleType) -> None:
    cache_backends = set([cast(str, cache["BACKEND"]) for cache in django.conf.settings.CACHES.values()])
    for cache_path in cache_backends:
        for method_name in ["get", "set", "add", "delete", "incr", "decr", "get_many", "set_many", "delete_many"]:
            try:
                cls: Type[Any] = django.utils.module_loading.import_string(cache_path)
                method: Optional[FunctionType] = getattr(cls, method_name, None)
                if method and not is_wrapped_with(method, traced_cache):
                    wrap(method, traced_cache)
            except Exception:
                log.debug("Error instrumenting cache %r", cache_path, exc_info=True)
