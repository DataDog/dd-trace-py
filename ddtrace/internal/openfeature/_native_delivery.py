"""
Native-owned CDN source delivery for Feature Flagging and Experimentation.
"""

import typing as t

from ddtrace.internal.openfeature._native import VariationType
from ddtrace.internal.openfeature._source import FeatureFlagCdnConfig


class NativeOwnedCdnSource:
    def __init__(
        self,
        config: FeatureFlagCdnConfig,
        delivery_cls: t.Optional[type] = None,
    ):
        self._config = config
        source_delivery_cls = delivery_cls or _native_delivery_cls()
        self._delivery = source_delivery_cls(
            config.base_url,
            config.api_key,
            config.poll_interval_seconds,
            config.request_timeout_seconds,
            config.max_retries,
            config.backoff_base_seconds,
        )

    @property
    def is_ready(self) -> bool:
        return bool(getattr(self._delivery, "is_ready", False))

    @property
    def is_started(self) -> bool:
        return bool(getattr(self._delivery, "is_started", False))

    def poll_once(self):
        return self._delivery.poll_once()

    def start(self):
        return self._delivery.start()

    def shutdown(self, timeout: t.Optional[float] = None) -> bool:
        status = self._delivery.shutdown(0.0 if timeout is None else timeout)
        return status.error is None

    def resolve_flag(
        self,
        flag_key: str,
        expected_type: VariationType,
        evaluation_context: t.Any,
    ):
        return self._delivery.resolve_value(flag_key, expected_type, _context_dict(evaluation_context))


def _context_dict(context: t.Any) -> dict[str, t.Any]:
    context_dict: dict[str, t.Any] = {"targeting_key": None, "attributes": {}}

    if context is None:
        return context_dict
    if isinstance(context, dict):
        targeting_key = context.get("targetingKey")
        if targeting_key is None:
            targeting_key = context.get("targeting_key")
        if targeting_key is not None:
            context_dict["targeting_key"] = targeting_key
        context_dict["attributes"] = context.get("attributes", {})
        return context_dict
    if hasattr(context, "targeting_key"):
        if context.targeting_key is not None:
            context_dict["targeting_key"] = context.targeting_key
        if hasattr(context, "attributes") and context.attributes:
            context_dict["attributes"] = context.attributes
    return context_dict


def _native_delivery_cls() -> type:
    from ddtrace.internal.native._native import ffe

    return ffe.NativeSourceDelivery
