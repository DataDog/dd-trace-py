from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401
from typing import cast  # noqa:F401

from ddtrace.internal import _service_state
from ddtrace.internal.constants import _SERVICE_SOURCE
from ddtrace.internal.settings._config import config


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.pin import Pin  # noqa:F401
    from ddtrace.internal.settings.integration import IntegrationConfig  # noqa:F401
    from ddtrace.trace import Span  # noqa:F401


def int_service(pin: Optional["Pin"], int_config: "IntegrationConfig", default: Optional[str] = None) -> Optional[str]:
    """Returns the service name for an integration which is internal
    to the application. Internal meaning that the work belongs to the
    user's application. Eg. Web framework, sqlalchemy, web servers.

    For internal integrations we prioritize overrides, then global defaults and
    lastly the default provided by the integration.
    """
    # Pin has top priority since it is user defined in code
    if pin is not None and pin.service:
        return pin.service

    # Config is next since it is also configured via code
    # Note that both service and service_name are used by
    # integrations.
    if "service" in int_config and int_config.service is not None:
        return cast(str, int_config.service)
    if "service_name" in int_config and int_config.service_name is not None:
        return cast(str, int_config.service_name)

    global_service = int_config.global_config._get_service()
    # We check if global_service != _inferred_base_service since global service (config.service)
    # defaults to _inferred_base_service when no DD_SERVICE is set. In this case, we want to not
    # use the inferred base service value, and instead use the integration default service. If we
    # didn't do this, we would have a massive breaking change from adding inferred_base_service.
    if global_service and global_service != int_config.global_config._inferred_base_service:
        return cast(str, global_service)

    if "_default_service" in int_config and int_config._default_service is not None:
        return cast(str, int_config._default_service)

    if default is None and global_service:
        return cast(str, global_service)

    return default


def ext_service(pin: Optional["Pin"], int_config: "IntegrationConfig", default: Optional[str] = None) -> Optional[str]:
    """Returns the service name for an integration which is external
    to the application. External meaning that the integration generates
    spans wrapping code that is outside the scope of the user's application. Eg. A database, RPC, cache, etc.
    """
    if pin is not None and pin.service:
        return pin.service

    if "service" in int_config and int_config.service is not None:
        return cast(str, int_config.service)
    if "service_name" in int_config and int_config.service_name is not None:
        return cast(str, int_config.service_name)

    if "_default_service" in int_config and int_config._default_service is not None:
        return cast(str, int_config._default_service)

    # A default is required since it's an external service.
    return default


def set_service_and_source(
    span: "Span",
    service: str,
    int_config: Union["IntegrationConfig", "dict[str, Any]"],
    default_service_key: str = "_default_service",
) -> None:
    service_source = ""
    mapped_service = config.service_mapping.get(service, service)
    if service != mapped_service:
        service_source = "opt.service_mapping"
        service = mapped_service
    elif int_config.get("split_by_domain", False):
        service_source = "opt.split_by_domain"
    # NB "not service" here makes svc_src make sense in cases of service inheritance
    elif not service or service == int_config.get(default_service_key):
        service_source = getattr(
            int_config,
            "integration_name",
            int_config.get("integration_name", "") if hasattr(int_config, "get") else "",
        )
    elif _service_state.is_user_provided_service():
        service_source = "m"
    if service_source:
        span.set_tag(_SERVICE_SOURCE, service_source)
    if service:
        span.service = service
