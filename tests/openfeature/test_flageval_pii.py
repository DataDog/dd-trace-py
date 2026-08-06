"""
Tests for the cross-SDK PII contract in the flagevaluation EVP track.

Every SDK produces the same digest for the same subject, so hashed values join
across languages. This file pins that contract for dd-trace-py.
"""

from ddtrace.internal.openfeature._flageval_pii import TARGETING_KEY_HASH_PREFIX
from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key


# Canonical cross-SDK vector. Every SDK must reproduce this digest byte-for-byte
# for the same subject. Asserted here and in system-tests
# (tests/ffe/test_flag_eval_evp.py, once the manifest is flipped).
PII_CANONICAL_TARGETING_KEY = "jane.doe@datadoghq.com"
PII_CANONICAL_HASHED = "sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"


class TestHashTargetingKey:
    def test_canonical_vector(self):
        """The single load-bearing cross-SDK assertion."""
        assert hash_targeting_key(PII_CANONICAL_TARGETING_KEY) == PII_CANONICAL_HASHED

    def test_prefix_length_and_charset(self):
        """71 chars total, sha256_ prefix, 64 lowercase-hex digest."""
        got = hash_targeting_key(PII_CANONICAL_TARGETING_KEY)
        assert len(got) == 71
        assert got.startswith(TARGETING_KEY_HASH_PREFIX)
        hex_suffix = got[len(TARGETING_KEY_HASH_PREFIX) :]
        assert len(hex_suffix) == 64
        assert all(c in "0123456789abcdef" for c in hex_suffix)

    def test_empty_input_stays_empty(self):
        """Absent targeting_key stays absent -- must NOT fabricate a shared pseudo-subject."""
        assert hash_targeting_key("") == ""

    def test_does_not_normalize(self):
        """Every variant must produce a DIFFERENT digest from the canonical one.

        Trimming, case folding, or Unicode normalization would silently break the
        cross-SDK join. NFC vs NFD is the subtle case: same grapheme, different bytes.
        """
        # NFC precomposed U+00E9 vs NFD "e" + U+0301 combining acute. Use explicit
        # escapes so a text-editor autonormalize can't collapse the two.
        nfc_accent = "josé@datadoghq.com"
        nfd_accent = "josé@datadoghq.com"
        assert nfc_accent.encode("utf-8") != nfd_accent.encode("utf-8"), (
            "NFC and NFD forms must have distinct UTF-8 bytes for this test to be meaningful"
        )

        variants = {
            "leading whitespace": " " + PII_CANONICAL_TARGETING_KEY,
            "trailing whitespace": PII_CANONICAL_TARGETING_KEY + " ",
            "uppercased": PII_CANONICAL_TARGETING_KEY.upper(),
            "NFC-composed accent": nfc_accent,
            "NFD-decomposed accent": nfd_accent,
        }
        seen = {PII_CANONICAL_HASHED: "canonical"}
        for name, input_str in variants.items():
            got = hash_targeting_key(input_str)
            assert got not in seen, f"{name} produced the same digest as {seen[got]}"
            seen[got] = name
