from types import TracebackType
from typing import Callable
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.cache import CacheCommandEvent
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_cache_operation


def _extract_connection_tags(
    event: CacheCommandEvent,
) -> dict:
    if event.connection_provider is None or event.db_tag_name is None:
        return {}

    try:
        connection_kwargs = event.connection_provider.connection_pool.connection_kwargs
        tags = {
            net.TARGET_HOST: connection_kwargs["host"],
            net.TARGET_PORT: connection_kwargs["port"],
            net.SERVER_ADDRESS: connection_kwargs["host"],
            event.db_tag_name: connection_kwargs.get("db") or 0,
        }
        client_name = connection_kwargs.get("client_name")
        if client_name and event.client_name_tag_name is not None:
            tags[event.client_name_tag_name] = client_name
        return tags
    except Exception:
        return {}


class CacheTracingSubscriber(TracingSubscriber):
    event_names = (CacheCommandEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: CacheCommandEvent = ctx.event
        span = ctx.span

        span._set_attribute(db.SYSTEM, event.db_system)

        for key, value in _extract_connection_tags(event).items():
            span._set_attribute(key, value)

        if event.query is not None and event.raw_command_tag_name is not None:
            raw_command_tag = cast(Callable[[str, Optional[str]], str], schematize_cache_operation)(
                event.raw_command_tag_name, event.cache_provider
            )
            span._set_attribute(raw_command_tag, event.query)

        if event.args_len is not None and event.args_len_tag_name is not None:
            span._set_attribute(event.args_len_tag_name, event.args_len)

        if event.pipeline_len is not None and event.pipeline_len_tag_name is not None:
            span._set_attribute(event.pipeline_len_tag_name, event.pipeline_len)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: CacheCommandEvent = ctx.event

        if event.rowcount is not None:
            ctx.span._set_attribute(db.ROWCOUNT, event.rowcount)
