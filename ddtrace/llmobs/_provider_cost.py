"""Normalize explicitly supplied provider billing without making provider requests."""

import hashlib
import math
from typing import Any
from typing import Optional

from ddtrace.llmobs._event_types import LLMObsSpanEvent


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _valid_amount(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        and value < 2**63 / 1e9
        and math.isfinite(value)
        and value * 1e9 < 2**63
    )


def _owner(ml_app: str, conversation_id: str) -> tuple[str, str]:
    parts = ("llmobs.provider_cost.v1", ml_app, "elevenlabs", conversation_id, "conversation")
    encoded = b"".join(str(len(part.encode("utf-8"))).encode("ascii") + b":" + part.encode("utf-8") for part in parts)
    digest = hashlib.sha256(encoded).digest()
    return digest[:16].hex(), str(int.from_bytes(digest[16:24], "big") or 1)


def build_conversation_cost_event(
    conversation: Any,
    ml_app: str,
    *,
    provider: str = "elevenlabs",
    source: str = "conversation_details",
    total_cost: Optional[float] = None,
    service: Optional[str] = None,
    env: Optional[str] = None,
    version: Optional[str] = None,
) -> Optional[LLMObsSpanEvent]:
    if provider != "elevenlabs" or source not in ("conversation_details", "post_call_transcription"):
        return None
    if not isinstance(ml_app, str) or not ml_app:
        return None
    conversation_id = _field(conversation, "conversation_id")
    status = _field(conversation, "status")
    if (
        not isinstance(conversation_id, str)
        or not 0 < len(conversation_id.encode("utf-8")) <= 256
        or status not in ("done", "failed")
    ):
        return None
    metadata = _field(conversation, "metadata")
    start = _field(metadata, "start_time_unix_secs")
    duration = _field(metadata, "call_duration_secs")
    if (
        type(start) is not int
        or not 0 < start < 2**64 / 1e9
        or type(duration) is not int
        or not 0 <= duration < 2**64 / 1e9
    ):
        return None
    charging = _field(metadata, "charging")
    amounts: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for name, value, path in (
        ("total", _field(metadata, "cost_fiat"), "metadata.cost_fiat"),
        ("llm", _field(charging, "llm_price"), "metadata.charging.llm_price"),
        ("platform", _field(charging, "platform_price"), "metadata.charging.platform_price"),
    ):
        if value is None:
            continue
        if not _valid_amount(value):
            return None
        amounts[name] = value
        provenance[name] = {"kind": source, "field": path}
    if (not amounts and total_cost is None) or (total_cost is not None and not _valid_amount(total_cost)):
        return None
    trace_id, span_id = _owner(ml_app, conversation_id)
    tags = ["ml_app:" + ml_app, "language:python", "integration:elevenlabs", "source:integration"]
    for name, value in (("service", service), ("env", env), ("version", version)):
        if value is not None:
            tags.append(name + ":" + value)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": "undefined",
        "name": "elevenlabs conversation billing",
        "session_id": conversation_id,
        "start_ns": start * 10**9,
        "duration": max(1, duration * 10**9),
        "status": "ok",
        "tags": tags,
        "metrics": {} if total_cost is None else {"total_cost": total_cost},
        "meta": {
            "span": {"kind": "workflow"},
            "metadata": {
                "ttfa_eligible": False,
                "provider_cost": {
                    "version": 1,
                    "provider": "elevenlabs",
                    "currency": "USD",
                    "scope": "conversation",
                    "status": status,
                    "conversation_id": conversation_id,
                    "owner": {"trace_id": trace_id, "span_id": span_id},
                    "amounts": amounts,
                    "provenance": provenance,
                },
            },
        },
        "_dd": {},
    }
