from collections.abc import Iterable
from contextlib import contextmanager
import random

import pylibmc
from wrapt import ObjectProxy

# project
import ddtrace
from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.pylibmc.addrs import parse_addresses
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import memcached
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.schema import schematize_service_name


# Original Client class
_Client = pylibmc.Client


log = get_logger(__name__)


class TracedClient(ObjectProxy):
    """TracedClient is a proxy for a pylibmc.Client that times it's network operations."""

    def __init__(self, client=None, service=memcached.SERVICE, tracer=None, *args, **kwargs):
        """Create a traced client that wraps the given memcached client."""
        # The client instance/service/tracer attributes are kept for compatibility
        # with the old interface: TracedClient(client=pylibmc.Client(['localhost:11211']))
        # TODO(Benjamin): Remove these in favor of patching.
        if not isinstance(client, _Client):
            # We are in the patched situation, just pass down all arguments to the pylibmc.Client
            # Note that, in that case, client isn't a real client (just the first argument)
            client = _Client(kwargs.get("servers", client), **{k: v for k, v in kwargs.items() if k != "servers"})
        else:
            log.warning(
                "TracedClient instantiation is deprecated and will be remove "
                "in future versions (0.6.0). Use patching instead (see the docs)."
            )

        super(TracedClient, self).__init__(client)

        schematized_service = schematize_service_name(service)
        pin = ddtrace.trace.Pin(service=schematized_service)
        pin._tracer = tracer
        pin.onto(self)

        # attempt to collect the pool of urls this client talks to
        try:
            self._addresses = parse_addresses(client.addresses)
        except Exception:
            log.debug("error setting addresses", exc_info=True)

    def clone(self, *args, **kwargs):
        # rewrap new connections.
        cloned = self.__wrapped__.clone(*args, **kwargs)
        traced_client = TracedClient(cloned)
        pin = ddtrace.trace.Pin.get_from(self)
        if pin:
            pin.clone().onto(traced_client)
        return traced_client

    def add(self, *args, **kwargs):
        return self._trace_cmd("add", *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._trace_cmd("get", *args, **kwargs)

    def set(self, *args, **kwargs):
        return self._trace_cmd("set", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._trace_cmd("delete", *args, **kwargs)

    def gets(self, *args, **kwargs):
        return self._trace_cmd("gets", *args, **kwargs)

    def touch(self, *args, **kwargs):
        return self._trace_cmd("touch", *args, **kwargs)

    def cas(self, *args, **kwargs):
        return self._trace_cmd("cas", *args, **kwargs)

    def incr(self, *args, **kwargs):
        return self._trace_cmd("incr", *args, **kwargs)

    def decr(self, *args, **kwargs):
        return self._trace_cmd("decr", *args, **kwargs)

    def append(self, *args, **kwargs):
        return self._trace_cmd("append", *args, **kwargs)

    def prepend(self, *args, **kwargs):
        return self._trace_cmd("prepend", *args, **kwargs)

    def get_multi(self, *args, **kwargs):
        return self._trace_multi_cmd("get_multi", *args, **kwargs)

    def set_multi(self, *args, **kwargs):
        return self._trace_multi_cmd("set_multi", *args, **kwargs)

    def delete_multi(self, *args, **kwargs):
        return self._trace_multi_cmd("delete_multi", *args, **kwargs)

    def _trace_cmd(self, method_name, *args, **kwargs):
        """trace the execution of the method with the given name and will
        patch the first arg.
        """
        method = getattr(self.__wrapped__, method_name)
        with self._span(method_name) as span:
            result = method(*args, **kwargs)
            if span is None:
                return result

            if args:
                span.set_tag_str(memcached.QUERY, "%s %s" % (method_name, args[0]))
            if method_name == "get":
                span.set_metric(db.ROWCOUNT, 1 if result else 0)
            elif method_name == "gets":
                # returns a tuple object that may be (None, None)
                span.set_metric(db.ROWCOUNT, 1 if isinstance(result, Iterable) and len(result) > 0 and result[0] else 0)
            return result

    def _trace_multi_cmd(self, method_name, *args, **kwargs):
        """trace the execution of the multi command with the given name."""
        method = getattr(self.__wrapped__, method_name)
        with self._span(method_name) as span:
            result = method(*args, **kwargs)
            if span is None:
                return result

            pre = kwargs.get("key_prefix")
            if pre:
                span.set_tag_str(memcached.QUERY, "%s %s" % (method_name, pre))

            if method_name == "get_multi":
                # returns mapping of key -> value if key exists, but does not include a missing key. Empty result = {}
                span.set_metric(
                    db.ROWCOUNT, sum(1 for doc in result if doc) if result and isinstance(result, Iterable) else 0
                )
            return result

    @contextmanager
    def _no_span(self):
        yield None

    def _span(self, cmd_name):
        """Return a span timing the given command."""
        pin = ddtrace.trace.Pin.get_from(self)
        if not pin or not pin.enabled():
            return self._no_span()

        span = pin.tracer.trace(
            schematize_cache_operation("memcached.cmd", cache_provider="memcached"),
            service=pin.service,
            resource=cmd_name,
            span_type=SpanTypes.CACHE,
        )

        span.set_tag_str(COMPONENT, config.pylibmc.integration_name)
        span.set_tag_str(db.SYSTEM, memcached.DBMS_NAME)

        # set span.kind to the type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(_SPAN_MEASURED_KEY)

        try:
            self._tag_span(span)
        except Exception:
            log.debug("error tagging span", exc_info=True)
        return span

    def _tag_span(self, span):
        # FIXME[matt] the host selection is buried in c code. we can't tell what it's actually
        # using, so fallback to randomly choosing one. can we do better?
        if self._addresses:
            _, host, port, _ = random.choice(self._addresses)  # nosec
            span.set_tag_str(net.TARGET_HOST, host)
            span.set_tag(net.TARGET_PORT, port)
            span.set_tag_str(net.SERVER_ADDRESS, host)
