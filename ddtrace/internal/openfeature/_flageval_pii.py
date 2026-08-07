"""
Cross-SDK PII fingerprint for the flagevaluation EVP track.

Hashing runs at flush cadence (once per aggregation bucket, off the evaluation
hot path). See docs/superpowers/specs/2026-08-06-pii-flagevaluations-hashing-design.md
for the full contract.
"""

import hashlib


# Literal prefix on every hashed targeting key; part of the wire contract.
# 71 chars total: 7 (prefix) + 64 (lowercase hex sha256 digest).
TARGETING_KEY_HASH_PREFIX = "sha256_"


def hash_targeting_key(raw: str) -> str:
    """
    Produce the cross-SDK fingerprint for a targeting key.

    Unsalted SHA-256 over the raw UTF-8 bytes -- no trimming, case folding, or
    Unicode normalization -- so every SDK produces a byte-identical digest and
    hashed values join across languages.

    Returns "" for empty input. Hashing "" would invent a shared pseudo-subject
    and corrupt unique-subject counts; an absent targeting_key is schema-valid
    (the degraded tier omits it too).
    """
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return TARGETING_KEY_HASH_PREFIX + digest
