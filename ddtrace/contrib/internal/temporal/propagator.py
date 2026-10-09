"""Datadog propagation wrapper for Temporal headers."""

from typing import Any
from typing import cast

import temporalio.api.common.v1
import temporalio.converter

from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator

from .constants import BAGGAGE_ITEM_SERVICE
from .constants import Carrier
from .constants import StringHeader
from .constants import TemporalHeader


log = get_logger(__name__)


class _Propagator:
    """Wraps HTTPPropagator with Temporal header encode/decode logic."""

    def __init__(
        self,
        *,
        header_key: str,
        service_name: str | None,
        payload_converter: temporalio.converter.PayloadConverter,
    ) -> None:
        self.header_key = header_key
        self.service_name = service_name
        self._payload_converter = payload_converter

    @staticmethod
    def get_baggage(ctx: Any) -> str | None:
        if ctx is None:
            return None

        getter = getattr(ctx, "get_baggage_item", None)
        if callable(getter):
            return cast(str | None, getter(BAGGAGE_ITEM_SERVICE))

        return None

    def set_baggage(self, ctx: Any) -> None:
        if self.service_name is None:
            return
        setter = getattr(ctx, "set_baggage_item", None)
        if callable(setter):
            setter(BAGGAGE_ITEM_SERVICE, self.service_name)

    def inject(self, context: Any) -> Carrier:
        carrier: Carrier = {}
        if context is None:
            return carrier
        HTTPPropagator.inject(context, carrier)
        return carrier

    def extract(self, header: StringHeader | None) -> Any:
        if header is None:
            return None

        try:
            ctx = HTTPPropagator.extract(header)  # type: ignore[no-untyped-call]
        except Exception:
            log.debug("Failed to extract Datadog context from Temporal header", exc_info=True)
            return None

        if ctx is None or getattr(ctx, "trace_id", None) is None:
            return None

        return ctx

    def _carrier_to_payload(self, carrier: Carrier) -> temporalio.api.common.v1.Payload:
        return self._payload_converter.to_payloads([carrier])[0]

    def _payload_to_carrier(self, payload: temporalio.api.common.v1.Payload) -> Carrier | None:
        decoded = self._payload_converter.from_payloads([payload])[0]
        if not isinstance(decoded, dict):
            return None
        return cast(Carrier, {str(k): str(v) for k, v in decoded.items()})

    def inject_headers(
        self,
        headers: TemporalHeader,
        context: Any,
    ) -> TemporalHeader:
        if context is None:
            return headers

        self.set_baggage(context)
        carrier = self.inject(context)

        return {**headers, self.header_key: self._carrier_to_payload(carrier)}

    def extract_headers(self, headers: TemporalHeader) -> Any:
        payload = headers.get(self.header_key)
        if payload is None:
            return None
        try:
            carrier = self._payload_to_carrier(payload)
        except Exception:
            log.debug("Failed to decode Temporal header %r", self.header_key, exc_info=True)
            return None
        return self.extract(carrier)
