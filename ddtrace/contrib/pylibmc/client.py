
# stdlib
import logging
import random

# 3p
from wrapt import ObjectProxy

# project
from ddtrace.ext import AppTypes
from ddtrace.ext import net
from .addrs import parse_addresses


log = logging.getLogger(__name__)


class TracedClient(ObjectProxy):
    """ TracedClient is a proxy for a pylibmc.Client that times it's network operations. """

    _service = None
    _tracer = None

    def __init__(self, client, tracer, service="memcached"):
        """ Create a traced client that wraps the given memcached client. """
        super(TracedClient, self).__init__(client)
        self._service = service
        self._tracer = tracer

        # attempt to collect the pool of urls this client talks to
        try:
            self._addresses = parse_addresses(client.addresses)
        except Exception:
            log.exception("error setting addresses")

        # attempt to set the service info
        try:
            self._tracer.set_service_info(
                service=service,
                app="memcached",
                app_type=AppTypes.cache)
        except Exception:
            log.exception("error setting service info")

    def clone(self, *args, **kwargs):
        # rewrap new connections.
        cloned = self.__wrapped__.clone(*args, **kwargs)
        return TracedClient(cloned, self._tracer, self._service)

    def get(self, *args, **kwargs):
        return self._trace("get", *args, **kwargs)

    def get_multi(self, *args, **kwargs):
        return self._trace("get_multi", *args, **kwargs)

    def set_multi(self, *args, **kwargs):
        return self._trace("set_multi", *args, **kwargs)

    def delete_multi(self, *args, **kwargs):
        self._trace("delete_multi", *args, **kwargs)

    def set(self, *args, **kwargs):
        return self._trace("set", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._trace("delete", *args, **kwargs)

    def gets(self, *args, **kwargs):
        return self._trace("gets", *args, **kwargs)

    def touch(self, *args, **kwargs):
        return self._trace("touch", *args, **kwargs)

    def cas(self, *args, **kwargs):
        return self._trace("cas", *args, **kwargs)

    def incr(self, *args, **kwargs):
        return self._trace("incr", *args, **kwargs)

    def decr(self, *args, **kwargs):
        return self._trace("decr", *args, **kwargs)

    def append(self, *args, **kwargs):
        return self._trace("append", *args, **kwargs)

    def prepend(self, *args, **kwargs):
        return self._trace("prepend", *args, **kwargs)

    def _trace(self, method_name, *args, **kwargs):
        """ trace the execution of the method with the given name. """
        method = getattr(self.__wrapped__, method_name)
        with self._span(method_name):
            return method(*args, **kwargs)

    def _span(self, cmd_name):
        """ Return a span timing the given command. """
        span = self._tracer.trace(
            "memcached.cmd",
            service=self._service,
            resource=cmd_name,
            span_type="cache")

        try:
            self._tag_span(span)
        except Exception:
            log.exception("error tagging span")
        finally:
            return span

    def _tag_span(self, span):
        # FIXME[matt] the host selection is buried in c code. we can't tell what it's actually
        # using, so fallback to randomly choosing one. can we do better?
        if self._addresses:
            _, host, port, _ = random.choice(self._addresses)
            span.set_meta(net.TARGET_HOST, host)
            span.set_meta(net.TARGET_PORT, port)


