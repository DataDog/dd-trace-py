from distutils.version import LooseVersion

from ddtrace import config, Pin

from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...ext import SpanTypes, redis as redisx
from ..redis.util import format_command_args
from .. import trace_utils


config._add("txredisapi", dict(_default_service="redis",))


@trace_utils.with_traced_module
def traced_execute_command(txredisapi, pin, func, instance, args, kwargs):
    ctx = pin.tracer.get_call_context()

    with ctx.override_ctx_item("trace_deferreds", True):
        d = func(*args, **kwargs)
    s = getattr(d, "__ddspan", None)

    if s:
        s.name = redisx.CMD
        s.service = trace_utils.ext_service(pin, config.txredisapi)
        s.span_type = SpanTypes.REDIS

        s.set_tag(SPAN_MEASURED_KEY)
        s.resource = format_command_args(args)
        s.set_tag(redisx.RAWCMD, s.resource)
        if pin.tags:
            s.set_tags(pin.tags)
        s.set_metric(redisx.ARGS_LEN, len(args))
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.redis.get_analytics_sample_rate())
    return d


def patch():
    import txredisapi

    if getattr(txredisapi, "__datadog_patch", False):
        return

    version = LooseVersion(txredisapi.__version__)

    Pin().onto(txredisapi)

    if version >= LooseVersion("1.2"):
        trace_utils.wrap("txredisapi", "BaseRedisProtocol.execute_command", traced_execute_command(txredisapi))
    else:
        trace_utils.wrap("txredisapi", "RedisProtocol.execute_command", traced_execute_command(txredisapi))
    setattr(txredisapi, "__datadog_patch", True)


def unpatch():
    import txredisapi

    if not getattr(txredisapi, "__datadog_patch", False):
        return

    version = LooseVersion(txredisapi.__version__)

    if version >= LooseVersion("1.2"):
        trace_utils.unwrap(txredisapi.BaseRedisProtocol, "execute_command")
    else:
        trace_utils.unwrap(txredisapi.RedisProtocol, "execute_command")

    setattr(txredisapi, "__datadog_patch", False)
