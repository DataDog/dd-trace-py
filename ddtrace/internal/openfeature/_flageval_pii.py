"""Cross-SDK PII fingerprint for the flagevaluation EVP track.

Hashing runs once per aggregation bucket at flush cadence, off the evaluation
hot path.
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

    Returns "" for empty, non-string, or non-UTF-8-encodable input. Omitting
    an invalid targeting key is privacy-safe and prevents one malformed value
    from aborting the entire writer flush.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    digest = hashlib.sha256(encoded).hexdigest()
    return TARGETING_KEY_HASH_PREFIX + digest
