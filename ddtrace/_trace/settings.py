"""Tracer-specific config mixins for migrated integrations (`IntegrationEnvConfig` subclasses, see
ddtrace/internal/settings/integration.py).

Not every migrated integration is necessarily an APM trace integration -- a future profiling- or
LLM-Observability-only integration wouldn't need either mixin here -- so this lives under the
tracer product (`ddtrace/_trace/`), not the generic `ddtrace.internal.settings` package
`IntegrationEnvConfig` itself lives in. An integration that *does* trace requests/propagate context
depends on the tracer anyway, so this is a normal, one-way product dependency, not a layering
violation.
"""

from typing import Any
from typing import Optional

from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings.http import HttpConfig
from ddtrace.internal.settings.http import get_http_tag_query_string
from ddtrace.internal.settings.http import header_is_traced
from ddtrace.internal.settings.http import header_tag_name
from ddtrace.internal.settings.http import is_header_tracing_configured
from ddtrace.internal.settings.http import trace_query_string


class HttpIntegrationConfigMixin:
    """Mixed into an `IntegrationEnvConfig` subclass for integrations that trace HTTP requests,
    client or server. Provides `.http` plus the header/query-string tracing surface
    `IntegrationConfig` exposes for every integration today, even non-HTTP ones -- migrated
    integrations that aren't HTTP-based don't need this surface, which is why it isn't on
    `IntegrationEnvConfig` itself.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)  # cooperative: chains into IntegrationEnvConfig.__init__
        self.http = HttpConfig()

    @property
    def trace_query_string(self) -> Optional[bool]:
        return trace_query_string(self.http, config._http)

    @property
    def is_header_tracing_configured(self) -> bool:
        return is_header_tracing_configured(self.http, config._http)

    def header_is_traced(self, header_name: str) -> bool:
        return header_is_traced(self.http, config._http, header_name)

    def _header_tag_name(self, header_name: str) -> Optional[str]:
        return header_tag_name(self.http, config._http, header_name)

    @property
    def http_tag_query_string(self) -> bool:
        return get_http_tag_query_string(
            config._http_tag_query_string, getattr(self, "default_http_tag_query_string", None)
        )


class DistributedTracingConfigMixin:
    """Mixed into an `IntegrationEnvConfig` subclass for integrations that propagate or consume
    distributed trace context -- HTTP clients/servers, message queues, RPC frameworks. Only ~30 of
    111 contrib integrations reference `distributed_tracing` today (grep-verified), not close to
    universal (e.g. `jinja2`, `sqlite3`, `unittest` have no such concept), so this is a mixin rather
    than a field on `IntegrationEnvConfig` itself -- the same reasoning that kept `.hooks`/
    `.analytics_enabled` off that base too. Also not folded into `HttpIntegrationConfigMixin`:
    message-queue integrations (`kafka`, `kombu`, `celery`, `aiobotocore`, `google_cloud_pubsub`)
    need this without being HTTP-based, so the two are orthogonal, not nested, capabilities.

    A migrated integration whose default differs from `True` overrides it by simply redeclaring the
    field with its own default, the same as any other envier field on a mixin base.
    """

    distributed_tracing = DDConfig.v(bool, "distributed_tracing", default=True)
