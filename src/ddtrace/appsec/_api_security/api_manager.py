import base64
import gzip
import os
from typing import TYPE_CHECKING

from ddtrace._tracing._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace.appsec import processor as appsec_processor
from ddtrace.appsec._api_security.schema import get_json_schema
from ddtrace.appsec._asm_request_context import _WAF_RESULTS
from ddtrace.appsec._asm_request_context import add_context_callback
from ddtrace.appsec._asm_request_context import remove_context_callback
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal.logger import get_logger
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.internal.service import Service


if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional


log = get_logger(__name__)
metrics = Metrics(namespace="datadog.api_security")
_sentinel = object()


class TooLargeSchemaException(Exception):
    pass


class APIManager(Service):
    COLLECTED = [
        (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, API_SECURITY.REQUEST_HEADERS_NO_COOKIES, dict),
        (SPAN_DATA_NAMES.REQUEST_QUERY, API_SECURITY.REQUEST_QUERY, dict),
        (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, API_SECURITY.REQUEST_PATH_PARAMS, dict),
        (SPAN_DATA_NAMES.REQUEST_BODY, API_SECURITY.REQUEST_BODY, None),
        (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, API_SECURITY.RESPONSE_HEADERS_NO_COOKIES, dict),
        (SPAN_DATA_NAMES.RESPONSE_BODY, API_SECURITY.RESPONSE_BODY, None),
    ]
    GLOBAL_RATE_LIMIT = 50.0  # requests per seconds

    INTERVAL_PER_ROUTE = 15.0  # seconds between requests

    _instance = None  # type: Optional[APIManager]

    @classmethod
    def enable(cls):
        # type: () -> None
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        log.debug("Enabling %s", cls.__name__)
        metrics.enable()
        cls._instance = cls()
        cls._instance.start()
        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return

        log.debug("Disabling %s", cls.__name__)
        cls._instance.stop()
        cls._instance = None
        metrics.disable()
        log.debug("%s disabled", cls.__name__)

    def __init__(self):
        # type: () -> None
        super(APIManager, self).__init__()
        try:
            self.INTERVAL_PER_ROUTE = float(os.environ.get(API_SECURITY.INTERVAL_PER_ROUTE, "15.0"))
        except BaseException:
            pass

        self._schema_meter = metrics.get_meter("schema")
        self._log_limiter = RateLimiter(limit_rate=1.0, raise_on_exceed=False)
        self._global_rate_limiter = RateLimiter(
            limit_rate=self.GLOBAL_RATE_LIMIT,
            raise_on_exceed=False,
        )
        self._rate_limiter_by_route = dict()  # type: Dict[str, RateLimiter]

        log.debug("%s initialized", self.__class__.__name__)

    def _stop_service(self):
        # type: () -> None
        remove_context_callback(self._schema_callback, global_callback=True)

    def _start_service(self):
        # type: () -> None
        add_context_callback(self._schema_callback, global_callback=True)

    def _should_collect_schema(self, env):
        if self.INTERVAL_PER_ROUTE <= 0.0:
            return True
        method = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_METHOD)
        route = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_ROUTE)
        # Framework is not fully supported
        if not method or not route:
            log.debug("unsupported groupkey for api security [method %s] [route %s]", bool(method), bool(route))
            return False
        # WAF has triggered, always collect schemas
        results = env.telemetry.get(_WAF_RESULTS)
        if results and any((result.data for result in results[0])):
            return True
        # Rate limit per route
        key = method + route
        r = self._rate_limiter_by_route.get(key)
        if r is None:
            self._rate_limiter_by_route[key] = r = RateLimiter(
                limit_rate=1 / self.INTERVAL_PER_ROUTE,
                tau=self.INTERVAL_PER_ROUTE,
                raise_on_exceed=False,
            )
        return r.limit() is not RateLimitExceeded

    def _schema_callback(self, env):
        if env.span is None:
            return
        root = env.span._local_root or env.span
        if not root or any(meta_name in root._meta for _, meta_name, _ in self.COLLECTED):
            return

        root._metrics[API_SECURITY.ENABLED] = 1.0

        try:
            if not self._should_collect_schema(env) or self._global_rate_limiter.limit() is RateLimitExceeded:
                return
        except Exception:
            self._log_limiter.limit(log.warning, "Failed to sample request for schema generation", exc_info=True)

        # we need the request content type on the span
        try:
            headers = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, _sentinel)
            if headers is not _sentinel:
                appsec_processor._set_headers(root, headers, kind="request")
        except Exception:
            self._log_limiter.limit(log.debug, "Failed to enrich request span with headers", exc_info=True)

        for address, meta_name, transform in self.COLLECTED:
            value = env.waf_addresses.get(address, _sentinel)
            if value is _sentinel:
                log.debug("no value for %s", address)
                continue
            json_serialized = ()
            b64_gzip_content = b""
            try:
                if transform is not None:
                    value = transform(value)
                json_serialized = get_json_schema(value)
                b64_gzip_content = base64.b64encode(gzip.compress(json_serialized.encode())).decode()
                if len(b64_gzip_content) >= MAX_SPAN_META_VALUE_LEN:
                    raise TooLargeSchemaException
                root._meta[meta_name] = b64_gzip_content
            except Exception as e:
                self._schema_meter.increment("errors", tags={"exc": e.__class__.__name__, "address": address})
                self._log_limiter.limit(
                    log.warning,
                    "Failed to get schema from %r [schema length=%d]:\n%s",
                    address,
                    len(b64_gzip_content),
                    repr(value)[:256],
                    exc_info=True,
                )
        self._schema_meter.increment("spans")
