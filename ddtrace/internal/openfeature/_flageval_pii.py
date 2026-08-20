"""Cross-SDK PII fingerprint for the flagevaluation EVP track.

Hashing runs once per aggregation bucket at flush cadence, off the evaluation
hot path.
"""

import hashlib


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
    Callers that must not raise use hash_targeting_key instead.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_targeting_key(raw: str) -> str:
    """
    Produce the prefixed cross-SDK fingerprint for the flagevaluation track.

    Wraps targeting_key_digest with the TARGETING_KEY_HASH_PREFIX and with
    input guards. The two share one digest body so the flagevaluation wire
    value and the span-enrichment tag value can never drift apart.

    Returns "" for empty, non-string, or non-UTF-8-encodable input. Omitting
    an invalid targeting key is privacy-safe and prevents one malformed value
    from aborting the entire writer flush.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        digest = targeting_key_digest(raw)
    except UnicodeEncodeError:
        return ""
    return TARGETING_KEY_HASH_PREFIX + digest
