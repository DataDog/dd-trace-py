"""Source-aware EVP route selection shared by Feature Flagging writers."""

import base64
from collections.abc import Callable
from dataclasses import dataclass
import errno
import http.client as httplib
import os
import socket
import time
from typing import Any
from typing import Optional
from typing import TypeVar
from urllib import request as urllib_request
from urllib.parse import unquote
from urllib.parse import urlsplit

from ddtrace import config as ddconfig
from ddtrace.internal import agent
from ddtrace.internal import forksafe
from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH_V4
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE
from ddtrace.internal.evp_proxy.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import TimedOutError
from ddtrace.internal.openfeature._agentless import normalize_agentless_site
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings.env import dd_environ
from ddtrace.internal.settings.openfeature import AGENTLESS
from ddtrace.internal.settings.openfeature import REMOTE_CONFIG
from ddtrace.internal.settings.openfeature import config as ffe_config
from ddtrace.internal.settings.openfeature import resolve_configuration_source
from ddtrace.internal.utils.http import get_connection
from ddtrace.version import __version__


log = get_logger(__name__)

DIRECT_INTAKE_PREFIX = "https://event-platform-intake."
DIRECT_RETRY_STATUSES = frozenset((404, 405))
DEFAULT_ROUTE_RECOVERY_INTERVAL = 30.0
EVP_ORIGIN_HEADERS = {
    "DD-EVP-ORIGIN": "dd-trace-py",
    "DD-EVP-ORIGIN-VERSION": __version__,
}
WINDOWS_WSAECONNREFUSED = 10061

_T = TypeVar("_T")


def _mark_direct_connection_untraced(connection: _T) -> _T:
    # AIDEV-NOTE: Direct EVP is internal telemetry. Mark every stdlib connection
    # so httplib instrumentation cannot create an application span or inject
    # trace headers into the authenticated request.
    setattr(connection, _HTTPLIB_NO_TRACE_REQUEST, True)
    return connection


def _build_direct_intake(site: str) -> Optional[str]:
    # AIDEV-NOTE: DD_SITE is user-controlled and this endpoint carries DD-API-KEY.
    # Keep the configured value confined to ASCII DNS labels so URL parsers cannot
    # reinterpret credentials, delimiters, percent escapes, or IDNA dot variants.
    try:
        normalized_site = normalize_agentless_site(site)
    except ValueError:
        return None

    hostname = "event-platform-intake.%s" % normalized_site
    intake = DIRECT_INTAKE_PREFIX + normalized_site
    try:
        parsed = urlsplit(intake)
        port = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc != hostname
        or parsed.hostname != hostname
    ):
        return None
    return intake


class AmbiguousLocalEVPDeliveryError(Exception):
    """A local delivery failure that may have happened after the batch was accepted."""


@dataclass(frozen=True)
class EVPRoute:
    """One immutable route snapshot used for the lifetime of an encoded batch."""

    intake: str
    base_path: str
    headers: dict[str, str]
    direct: bool = False
    agentless_local: bool = False
    fallback: Optional["EVPRoute"] = None

    def endpoint(self, product_path: str) -> str:
        return "%s%s" % (self.base_path.rstrip("/"), product_path)


def get_evp_connection(
    route: EVPRoute,
    timeout: float,
    connection_factory: Callable[..., Any] = get_connection,
) -> Any:
    """Return a connection for ``route``, tunneling direct HTTPS through the configured proxy.

    The shared native connection follows redirects automatically, so direct
    event delivery deliberately uses ``HTTPSConnection``. Its request API does
    not follow redirects, keeping ``DD-API-KEY`` confined to the validated
    intake origin. A non-default factory remains available for isolated tests.
    """
    if not route.direct:
        return connection_factory(route.intake, timeout=timeout)
    if connection_factory is not get_connection:
        return connection_factory(route.intake, timeout=timeout)

    origin = urlsplit(route.intake)
    if origin.scheme != "https" or origin.hostname is None:
        raise ValueError("Feature Flagging direct intake must use HTTPS")
    if urllib_request.proxy_bypass(origin.netloc):
        return _mark_direct_connection_untraced(
            httplib.HTTPSConnection(origin.hostname, origin.port or 443, timeout=timeout)
        )

    # DD_PROXY_HTTPS is the tracer-specific spelling used by system-tests and
    # HTTPS_PROXY/https_proxy are the standard process-wide equivalents.
    proxy_url = dd_environ.get("DD_PROXY_HTTPS") or urllib_request.getproxies().get("https")
    if not proxy_url:
        return _mark_direct_connection_untraced(
            httplib.HTTPSConnection(origin.hostname, origin.port or 443, timeout=timeout)
        )
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    proxy = urlsplit(proxy_url)
    if proxy.scheme != "http" or proxy.hostname is None:
        raise ValueError("Feature Flagging direct intake HTTPS proxy must use HTTP")

    tunnel_headers = {}
    if proxy.username is not None:
        credentials = "%s:%s" % (unquote(proxy.username), unquote(proxy.password or ""))
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        tunnel_headers["Proxy-Authorization"] = "Basic " + encoded

    connection = _mark_direct_connection_untraced(
        httplib.HTTPSConnection(proxy.hostname, proxy.port or 80, timeout=timeout)
    )
    connection.set_tunnel(origin.hostname, origin.port or 443, headers=tunnel_headers)
    return connection


def _is_definitive_pre_send_failure(error: BaseException) -> bool:
    if isinstance(error, ConnectionFailedError):
        return True
    if isinstance(error, (ConnectionRefusedError, FileNotFoundError)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno in (socket.EAI_AGAIN, socket.EAI_NONAME)
    return isinstance(error, OSError) and error.errno in (
        errno.ECONNREFUSED,
        errno.ENOENT,
        WINDOWS_WSAECONNREFUSED,
    )


def _is_ambiguous_io_failure(error: BaseException) -> bool:
    if isinstance(error, (TimedOutError, HttpIoError, TimeoutError, ConnectionResetError, BrokenPipeError)):
        return True
    return isinstance(error, OSError) and not _is_definitive_pre_send_failure(error)


class FeatureFlagEVPRouteSelector:
    """Discover one local route and coordinate safe direct-intake fallback."""

    def __init__(
        self,
        configuration_source: str,
        agent_url: str,
        api_key: Optional[str],
        site: str,
        info_provider: Callable[[str], Optional[dict[str, Any]]] = agent.info,
        clock: Callable[[], float] = time.monotonic,
        recovery_interval: float = DEFAULT_ROUTE_RECOVERY_INTERVAL,
    ) -> None:
        self._configuration_source = configuration_source
        self._agent_url = agent_url
        self._api_key = api_key
        self._site = site
        self._info_provider = info_provider
        self._clock = clock
        self._recovery_interval = recovery_interval
        self._lock = forksafe.Lock()
        self._pid = os.getpid()
        self._selected = False
        self._route: Optional[EVPRoute] = None
        self._recover_at = 0.0
        self._unavailable_warning_logged = False
        self._invalid_site_warning_logged = False

    def select(self) -> Optional[EVPRoute]:
        """Return the active route, serializing discovery across event writers."""
        with self._lock:
            self._reset_after_fork()

            # Remote Configuration preserves its historical Agent-only v2 path.
            # It never discovers v4, acquires direct credentials, or participates
            # in the Agentless unavailable-state cooldown.
            if self._configuration_source == REMOTE_CONFIG:
                if not self._selected:
                    self._selected = True
                    self._route = self._local_route(EVP_PROXY_AGENT_BASE_PATH)
                return self._route

            if self._configuration_source != AGENTLESS:
                return None

            if self._selected:
                if self._route is not None or self._clock() < self._recover_at:
                    return self._route
                self._selected = False

            self._selected = True
            local = self._discover_local_route()
            direct = self._direct_route()
            if local is not None:
                self._route = EVPRoute(
                    intake=local.intake,
                    base_path=local.base_path,
                    headers=local.headers,
                    agentless_local=True,
                    fallback=direct,
                )
            else:
                self._route = direct

            if self._route is None and not self._unavailable_warning_logged:
                self._unavailable_warning_logged = True
                log.warning(
                    "Feature Flagging event delivery is temporarily unavailable; local EVP discovery will retry "
                    "after the recovery interval"
                )
            if self._route is None:
                self._recover_at = self._clock() + self._recovery_interval
            else:
                self._recover_at = 0.0
            return self._route

    def send(self, route: EVPRoute, send_once: Callable[[EVPRoute], _T]) -> _T:
        """Send once, replaying through direct intake only after a definitive local rejection."""
        # AIDEV-NOTE: The caller snapshots route for the whole batch retry loop. On an
        # ambiguous failure we switch only future batches to direct intake, avoiding a
        # cross-route replay that could duplicate an event already accepted by the Agent.
        try:
            response = send_once(route)
        except Exception as error:
            fallback = route.fallback
            if fallback is not None and _is_definitive_pre_send_failure(error):
                self._activate_direct(route, fallback)
                return send_once(fallback)
            if fallback is not None and _is_ambiguous_io_failure(error):
                # The receiver may have accepted this batch. Mark this failure as terminal
                # for callers with same-batch retries; only later route snapshots may use
                # direct intake.
                self._activate_direct(route, fallback)
                raise AmbiguousLocalEVPDeliveryError("local EVP delivery outcome is unknown") from error
            if route.agentless_local:
                if fallback is not None:
                    self._activate_direct(route, fallback)
                else:
                    self._mark_unavailable(route)
            raise

        status = getattr(response, "status", None)
        if route.agentless_local and status in DIRECT_RETRY_STATUSES:
            if route.fallback is not None:
                self._activate_direct(route, route.fallback)
                return send_once(route.fallback)
            self._mark_unavailable(route)
        return response

    def _reset_after_fork(self) -> None:
        pid = os.getpid()
        if pid == self._pid:
            return
        self._pid = pid
        self._selected = False
        self._route = None
        self._recover_at = 0.0
        self._unavailable_warning_logged = False
        self._invalid_site_warning_logged = False

    def _discover_local_route(self) -> Optional[EVPRoute]:
        try:
            agent_info = self._info_provider(self._agent_url)
        except Exception:
            log.debug("Feature Flagging EVP route discovery failed", exc_info=True)
            return None

        raw_endpoints = agent_info.get("endpoints", ()) if agent_info else ()
        endpoints = raw_endpoints if isinstance(raw_endpoints, (list, tuple)) else ()
        advertised = {str(endpoint).rstrip("/") for endpoint in endpoints}
        base_path = None
        if EVP_PROXY_AGENT_BASE_PATH_V4 in advertised:
            base_path = EVP_PROXY_AGENT_BASE_PATH_V4
        elif EVP_PROXY_AGENT_BASE_PATH in advertised:
            base_path = EVP_PROXY_AGENT_BASE_PATH
        if base_path is None:
            return None
        return self._local_route(base_path)

    def _local_route(self, base_path: str) -> EVPRoute:
        agent_base_path = agent._agent_base_path(self._agent_url)
        return EVPRoute(
            intake=self._agent_url,
            base_path=agent_base_path + base_path,
            headers={
                "Content-Type": "application/json",
                EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_EVENT_PLATFORM_VALUE,
                **EVP_ORIGIN_HEADERS,
            },
        )

    def _direct_route(self) -> Optional[EVPRoute]:
        if not self._api_key:
            return None
        intake = _build_direct_intake(self._site)
        if intake is None:
            if not self._invalid_site_warning_logged:
                self._invalid_site_warning_logged = True
                log.warning("Feature Flagging direct event delivery disabled because DD_SITE is invalid")
            return None
        return EVPRoute(
            intake=intake,
            base_path="",
            headers={"Content-Type": "application/json", "DD-API-KEY": self._api_key, **EVP_ORIGIN_HEADERS},
            direct=True,
        )

    def _activate_direct(self, route: EVPRoute, fallback: EVPRoute) -> None:
        with self._lock:
            if self._route == route:
                self._route = fallback

    def _mark_unavailable(self, route: EVPRoute) -> None:
        with self._lock:
            if self._route == route:
                self._route = None
                self._recover_at = self._clock() + self._recovery_interval


_SELECTOR_LOCK = forksafe.Lock()
_SELECTOR: Optional[FeatureFlagEVPRouteSelector] = None


def get_feature_flag_evp_route_selector() -> FeatureFlagEVPRouteSelector:
    global _SELECTOR
    with _SELECTOR_LOCK:
        if _SELECTOR is None:
            _SELECTOR = FeatureFlagEVPRouteSelector(
                configuration_source=resolve_configuration_source(ffe_config),
                agent_url=agent_config.trace_agent_url,
                api_key=ddconfig._dd_api_key,
                site=ddconfig._dd_site,
            )
        return _SELECTOR


def reset_feature_flag_evp_route_selector() -> None:
    global _SELECTOR
    with _SELECTOR_LOCK:
        _SELECTOR = None
