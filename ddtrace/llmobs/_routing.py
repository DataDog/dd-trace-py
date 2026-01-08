import contextvars
from typing import Optional
from typing import TypedDict

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


class RoutingConfig(TypedDict, total=False):
    dd_api_key: str
    dd_site: Optional[str]


_DD_ROUTING_CONTEXTVAR: contextvars.ContextVar[Optional[RoutingConfig]] = contextvars.ContextVar(
    "datadog_llmobs_routing_contextvar",
    default=None,
)


class RoutingContext:
    """Context manager for setting routing configuration for LLMObs spans.

    All LLMObs spans created within this context will be routed to the
    specified Datadog organization.

    Example::

        with LLMObs.routing_context(dd_api_key="customer-api-key"):
            with LLMObs.workflow(name="process"):
                # spans go to customer's org
                pass

    """

    def __init__(self, dd_api_key: str, dd_site: Optional[str] = None) -> None:
        if not dd_api_key:
            raise ValueError("dd_api_key is required for routing context")
        self._config: RoutingConfig = {"dd_api_key": dd_api_key}
        if dd_site:
            self._config["dd_site"] = dd_site
        self._token: Optional[contextvars.Token[Optional[RoutingConfig]]] = None

    def __enter__(self) -> "RoutingContext":
        current = _DD_ROUTING_CONTEXTVAR.get()
        if current is not None:
            logger.warning(
                "Nested routing context detected. Inner context will override outer context. "
                "Spans created in the inner context will only be sent to the inner context."
            )
        self._token = _DD_ROUTING_CONTEXTVAR.set(self._config)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token is not None:
            _DD_ROUTING_CONTEXTVAR.reset(self._token)
            self._token = None

    async def __aenter__(self) -> "RoutingContext":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)


def _get_current_routing() -> Optional[RoutingConfig]:
    return _DD_ROUTING_CONTEXTVAR.get()
