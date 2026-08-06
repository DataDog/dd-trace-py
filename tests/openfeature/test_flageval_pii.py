"""
Tests for the cross-SDK PII contract in the flagevaluation EVP track.

Every SDK produces the same digest for the same subject, so hashed values join
across languages. This file pins that contract for dd-trace-py.
"""

from unittest.mock import MagicMock

import pytest

from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flageval_pii import TARGETING_KEY_HASH_PREFIX
from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._native import process_ffe_configuration


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
        nfc_accent = "jos\u00e9@datadoghq.com"
        nfd_accent = "jose\u0301@datadoghq.com"
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


class TestFfeSnapshot:
    """Storage semantics of _FfeSnapshot in _config.py."""

    def test_default_is_none(self):
        _set_ffe_config(None)
        try:
            assert _get_ffe_snapshot() is None
        finally:
            _set_ffe_config(None)

    def test_set_snapshot_round_trips(self):
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(_FfeSnapshot(config=fake_config, observe_full_evaluation_data=True))
        snap = _get_ffe_snapshot()
        try:
            assert snap is not None
            assert snap.config is fake_config
            assert snap.observe_full_evaluation_data is True
        finally:
            _set_ffe_config(None)

    def test_legacy_bare_config_is_consent_off(self):
        """A bare Configuration (existing test callers) is stored as consent-off."""
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(fake_config)
        snap = _get_ffe_snapshot()
        try:
            assert snap is not None
            assert snap.config is fake_config
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_get_ffe_config_returns_bare_config(self):
        """The legacy accessor still returns just the config."""
        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(_FfeSnapshot(config=fake_config, observe_full_evaluation_data=True))
        try:
            assert _get_ffe_config() is fake_config
        finally:
            _set_ffe_config(None)


class TestUFCObserveFullEvaluationDataParsing:
    """Read side of the contract: the field is read from the UFC ROOT (sibling
    of `environment`), and any non-True value fails closed to False.
    """

    def _minimal_ufc(self, extra_root: dict = None, environment_extra: dict = None) -> dict:
        env = {"name": "Staging"}
        if environment_extra:
            env.update(environment_extra)
        # Native parser requires id/createdAt at the UFC root alongside format,
        # environment, and flags. Keep the shape minimal but valid so the parse
        # succeeds and the snapshot lands, letting these tests focus on the
        # observeFullEvaluationData read path.
        ufc = {
            "id": "test-config-pii",
            "createdAt": "2026-08-06T00:00:00Z",
            "format": "SERVER",
            "environment": env,
            "flags": {},
        }
        if extra_root:
            ufc.update(extra_root)
        return ufc

    def _snapshot_for(self, ufc: dict):
        _set_ffe_config(None)
        process_ffe_configuration(ufc)
        return _get_ffe_snapshot()

    def test_absent_is_false(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc())
            assert snap is not None
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_explicit_false(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": False}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_explicit_true_opts_in(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": True}))
            assert snap.observe_full_evaluation_data is True
        finally:
            _set_ffe_config(None)

    def test_explicit_null_fails_closed(self):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": None}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, [], {}])
    def test_wrong_type_fails_closed(self, bad):
        try:
            snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": bad}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)

    def test_nested_under_environment_is_not_read(self):
        """FFL-2784 placement-drift regression guard: parser reading it from
        `environment` would report True here, hash forever in prod. The field
        lives at the UFC ROOT.
        """
        try:
            snap = self._snapshot_for(self._minimal_ufc(environment_extra={"observeFullEvaluationData": True}))
            assert snap.observe_full_evaluation_data is False
        finally:
            _set_ffe_config(None)


class TestProviderStampsConsent:
    """The provider stamps observe_full_evaluation_data on flag_metadata on every
    return path, from the exact snapshot the evaluation ran against.
    """

    def _config_with_consent(self, observe: bool):
        """Build a minimal UFC dict with the given consent value."""
        return {
            "id": "test-config",
            "createdAt": "2026-01-01T00:00:00Z",
            "format": "SERVER",
            "observeFullEvaluationData": observe,
            "environment": {"name": "Staging"},
            "flags": {
                "test-bool": {
                    "key": "test-bool",
                    "enabled": True,
                    "variationType": "BOOLEAN",
                    "defaultVariation": "on",
                    "variations": {"on": {"key": "on", "value": True}},
                    "allocations": [
                        {
                            "key": "default",
                            "rules": [],
                            "splits": [{"variationKey": "on", "shards": []}],
                            "doLog": True,
                        }
                    ],
                },
            },
        }

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    def _provider(self, monkeypatch):
        # Enable the provider so _resolve_details doesn't early-return DISABLED.
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        # Killswitch on for these tests -- we only care about the metadata stamp.
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")
        from ddtrace.internal.openfeature._provider import DataDogProvider

        return DataDogProvider()

    def test_success_path_stamps_consent_true(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_success_path_stamps_consent_false(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(False))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is False

    def test_flag_not_found_still_stamps_consent(self, monkeypatch):
        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("no-such-flag", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_no_configuration_fails_closed(self, monkeypatch):
        """PROVIDER_NOT_READY: no environment behind the evaluation, so no consent
        to honor. Must stamp False rather than leave the key absent-and-ambiguous.
        """
        # No process_ffe_configuration call -- snapshot stays None.
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("anything", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is False
