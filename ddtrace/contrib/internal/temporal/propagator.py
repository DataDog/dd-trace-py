"""Datadog propagation wrapper for Temporal headers."""

from typing import cast

import temporalio.api.common.v1
import temporalio.converter

from .constants import Carrier
from .constants import TemporalHeader


class _Propagator:
    """Converts trace carriers to and from Temporal payload headers."""

    def __init__(
        self,
        *,
        header_key: str,
        payload_converter: temporalio.converter.PayloadConverter,
        allow_invalid_parent_spans: bool = False,
    ) -> None:
        self.header_key = header_key
        self._payload_converter = payload_converter
        self.allow_invalid_parent_spans = allow_invalid_parent_spans

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
        carrier: Carrier,
    ) -> TemporalHeader:
        if not carrier:
            return headers
        return {**headers, self.header_key: self._carrier_to_payload(carrier)}

    def extract_headers(self, headers: TemporalHeader) -> Carrier | None:
        payload = headers.get(self.header_key)
        if payload is None:
            return None
        try:
            return self._payload_to_carrier(payload)
        except Exception:
            if self.allow_invalid_parent_spans:
                return None
            raise
