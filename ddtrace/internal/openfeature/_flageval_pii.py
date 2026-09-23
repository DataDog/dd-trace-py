"""Cross-SDK PII fingerprint for the flagevaluation EVP track.

The prefixed EVP targeting-key hash is applied during background aggregation,
off the evaluation hot path.
"""

import hashlib
import typing


# Literal prefix on every hashed targeting key; part of the wire contract.
# 71 chars total: 7 (prefix) + 64 (lowercase hex sha256 digest).
TARGETING_KEY_HASH_PREFIX = "sha256_"


def targeting_key_digest(raw: str) -> str:
    """
    Bare lowercase-hex SHA-256 digest of a targeting key, with no prefix.

    Unsalted SHA-256 over the raw UTF-8 bytes -- no trimming, case folding, or
    Unicode normalization -- so every SDK produces a byte-identical digest and
    hashed values join across languages. The digest is a pseudonym, not an
    anonymization: a deterministic cross-SDK join forbids a per-process salt,
    so a low-entropy subject identifier stays recoverable by dictionary attack.

    Raises on non-string input, matching the frozen span-enrichment contract.
    Callers that must not raise use prefixed_targeting_key_digest instead.
    """
    # Use the unbound built-ins so a str subclass cannot replace the bytes
    # used for the cross-SDK digest.
    normalized = str.__str__(raw)
    return hashlib.sha256(str.encode(normalized, "utf-8", errors="strict")).hexdigest()


def normalize_targeting_key(raw: typing.Any) -> typing.Optional[str]:
    """Return a strict UTF-8 targeting key, or None when it must be omitted.

    An explicit empty string is valid and must stay distinct from a missing,
    non-string, or malformed value.
    """
    if not isinstance(raw, str):
        return None
    normalized = str.__str__(raw)
    try:
        str.encode(normalized, "utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return normalized


def prefixed_targeting_key_digest(raw: typing.Any) -> typing.Optional[str]:
    """Produce the protected cross-SDK targeting-key representation.

    Wraps targeting_key_digest with the TARGETING_KEY_HASH_PREFIX and strict
    input guards. The two share one digest body so the flagevaluation wire
    value and the span-enrichment tag value can never drift apart.

    An explicit empty string remains empty. Missing, non-string, and malformed
    values return None so the serializer omits the field without dropping the
    event.
    """
    normalized = normalize_targeting_key(raw)
    if normalized is None or normalized == "":
        return normalized
    return TARGETING_KEY_HASH_PREFIX + targeting_key_digest(normalized)
