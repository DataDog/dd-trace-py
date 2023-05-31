from typing import TYPE_CHECKING

from ddtrace._tracing._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace.appsec._asm_request_context import _WAF_RESULTS
from ddtrace.appsec._asm_request_context import add_context_callback
from ddtrace.appsec._asm_request_context import remove_context_callback
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec.api_security.schema import get_json_schema
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


class TooLarge(Exception):
    pass


class APIManager(Service):

    COLLECTED = [
        (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, "_dd.schema.req.headers", dict),
        (SPAN_DATA_NAMES.REQUEST_QUERY, "_dd.schema.req.query", dict),
        (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, "_dd.schema.req.params", dict),
        (SPAN_DATA_NAMES.REQUEST_BODY, "_dd.schema.req.body", None),
        (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, "_dd.schema.res.headers", dict),
        (SPAN_DATA_NAMES.RESPONSE_BODY, "_dd.schema.res.body", None),
    ]
    GLOBAL_RATE_LIMIT = 50.0  # requests per seconds
    INTERVAL_PER_ROUTE = 15.0  # seconds between requests

    _instance = None  # type: Optional[APIManager]

    @classmethod
    def enable(cls):
        # type: () -> None
        if cls._instance is not None:
            log.info("%s already enabled", cls.__name__)
            return

        log.info("Enabling %s", cls.__name__)
        metrics.enable()
        cls._instance = cls()
        cls._instance.start()
        log.info("%s enabled", cls.__name__)

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            log.info("%s not enabled", cls.__name__)
            return

        log.info("Disabling %s", cls.__name__)
        cls._instance.stop()
        cls._instance = None
        metrics.disable()
        log.info("%s disabled", cls.__name__)

    def __init__(self):
        # type: () -> None
        super(APIManager, self).__init__()

        self._schema_meter = metrics.get_meter("schema")
        self._log_limiter = RateLimiter(limit_rate=1.0, raise_on_exceed=False)
        self._global_rate_limiter = RateLimiter(
            limit_rate=self.GLOBAL_RATE_LIMIT,
            raise_on_exceed=False,
        )
        self._rate_limiter_by_route = dict()  # type: Dict[str, RateLimiter]

        log.info("%s initialized", self.__class__.__name__)

    def _stop_service(self):
        # type: () -> None
        remove_context_callback(self._schema_callback, global_callback=True)

    def _start_service(self):
        # type: () -> None
        add_context_callback(self._schema_callback, global_callback=True)

    def _should_collect_schema(self, env):
        method = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_METHOD)
        route = env.waf_addresses.get(SPAN_DATA_NAMES.REQUEST_ROUTE)
        # Framework is not fully supported
        if not method or not route:
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
        if not root:
            return

        root._metrics["_dd.appsec.api_security.enabled"] = 1.0

        try:
            if not self._should_collect_schema(env) or self._global_rate_limiter.limit() is RateLimitExceeded:
                return
        except Exception:
            self._log_limiter.limit(log.warning, "Failed to sample request for schema generation", exc_info=True)

        for address, meta_name, transform in self.COLLECTED:
            _sentinel = object()
            value = env.waf_addresses.get(address, _sentinel)
            if value is _sentinel:
                continue
            try:
                if transform is not None:
                    value = transform(value)
                json_serialized = get_json_schema(value)
                if len(json_serialized) >= MAX_SPAN_META_VALUE_LEN:
                    raise TooLarge
                root._meta[meta_name] = json_serialized
            except Exception as e:
                self._schema_meter.increment("errors", tags={"exc": e.__class__.__name__, "address": address})
                self._log_limiter.limit(
                    log.warning, "Failed to get schema from %r:\n%s", address, repr(value)[:256], exc_info=True
                )
        self._schema_meter.increment("spans")
