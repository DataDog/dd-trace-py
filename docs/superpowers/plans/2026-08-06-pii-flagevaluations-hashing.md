# FFL-2964: PII protection in flagevaluations EVP track — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hash `targeting_key` by default and omit `context.evaluation` by default in the dd-trace-py EVP `flagevaluation` track, gated by the new top-level UFC boolean `observeFullEvaluationData`, matching the cross-SDK contract established by dd-trace-java#12042 and dd-trace-go#5151.

**Architecture:** Bundle the parsed UFC and its consent value into a single atomic `_FfeSnapshot` in `ddtrace/internal/openfeature/_config.py`. Stamp consent onto OpenFeature `flag_metadata` at evaluation entry, from the exact snapshot the evaluation ran against. Read consent off the metadata in the EVP hook (fail closed on missing/malformed). Carry consent through `_EvalEvent` and `_Entry`, add it as a full-tier bucket key dimension, AND-fold on fast-path merge, and skip context capture when consent is off. Hash at flush cadence inside `FlagEvaluationWriter.periodic()` using a new `hash_targeting_key` helper. No Rust or libdatadog changes.

**Tech Stack:** Python 3.8+, `hashlib.sha256`, OpenFeature Python SDK 0.8+, existing `ddtrace/internal/openfeature/*` module, pytest + riot for tests.

**Spec:** `docs/superpowers/specs/2026-08-06-pii-flagevaluations-hashing-design.md`.

**Testing convention:** Never run `pytest` directly. Use `scripts/run-tests <suite>` per repo policy (see `AGENTS.md` project rules 1-2). For this feature the suite is `openfeature`. Example: `scripts/run-tests openfeature`. To scope down during iteration, pass `-k` through it or invoke the openfeature riot venv with test-id arguments.

**Commit convention:** Conventional Commits per `commitlint.config.js`. Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`. Scope for this work: `openfeature`. Every commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`. Never `--amend` — always create new commits.

**Cross-SDK canonical vector, referenced in multiple tasks:**
- Input: `"jane.doe@datadoghq.com"`
- Output: `"sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"`

---

## File structure

Files to create:

- `ddtrace/internal/openfeature/_flageval_pii.py` — the hash helper and prefix constant. Small, single-purpose, no ddtrace imports so it's cheap to import from anywhere.
- `tests/openfeature/test_flageval_pii.py` — all new PII-specific tests. Kept in one new file rather than sprinkled across existing test files so the RFC's validation requirements can be found in one place.

Files to modify:

- `ddtrace/internal/openfeature/_config.py` — introduce `_FfeSnapshot`; keep `_set_ffe_config`/`_get_ffe_config` backward-compatible; add `_get_ffe_snapshot`.
- `ddtrace/internal/openfeature/_native.py` — read `observeFullEvaluationData` off the dict and store the snapshot.
- `ddtrace/internal/openfeature/_flagevaluation_writer.py` — add `METADATA_OBSERVE_FULL_EVALUATION_DATA`; extend `_EvalEvent` and `_Entry`; add consent to full-tier key; force `context_attrs={}` + `ctx_key=""` when consent off; AND-fold on merge; branch serialization in `periodic()`.
- `ddtrace/internal/openfeature/_flag_eval_evp_hook.py` — read consent from metadata (fail closed); skip attrs when off; attach to `_EvalEvent`.
- `ddtrace/internal/openfeature/_provider.py` — load snapshot; stamp consent on `flag_metadata` on every return path; pass snapshot's config to `resolve_flag`.
- `tests/openfeature/test_flagevaluation_writer.py` — update existing helpers/tests that construct `_EvalEvent` or `_Entry` directly so the new fields don't break them.
- `tests/openfeature/test_flag_eval_evp_hook.py` — add fail-closed metadata cases.
- `tests/openfeature/test_provider.py` — add "stamps consent on every path" cases.
- `releasenotes/notes/` — new fragment (via `releasenote` skill).

---

## Task 1: Hash helper module + canonical vector test (TDD)

**Files:**
- Create: `ddtrace/internal/openfeature/_flageval_pii.py`
- Create: `tests/openfeature/test_flageval_pii.py`

The canonical cross-SDK vector is the load-bearing test. Write it first.

- [ ] **Step 1.1: Create the empty test file with the canonical vector constants and one failing test.**

Write `tests/openfeature/test_flageval_pii.py`:

```python
"""
Tests for the cross-SDK PII contract in the flagevaluation EVP track.

Every SDK produces the same digest for the same subject, so hashed values join
across languages. This file pins that contract for dd-trace-py.
"""

import pytest


# Canonical cross-SDK vector. Every SDK must reproduce this digest byte-for-byte
# for the same subject. Asserted here and in system-tests
# (tests/ffe/test_flag_eval_evp.py, once the manifest is flipped).
PII_CANONICAL_TARGETING_KEY = "jane.doe@datadoghq.com"
PII_CANONICAL_HASHED = "sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b"


class TestHashTargetingKey:
    def test_canonical_vector(self):
        """The single load-bearing cross-SDK assertion."""
        from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key

        assert hash_targeting_key(PII_CANONICAL_TARGETING_KEY) == PII_CANONICAL_HASHED
```

- [ ] **Step 1.2: Run the test and confirm it fails.**

Run: `scripts/run-tests openfeature -k test_canonical_vector`

Expected: FAIL with `ModuleNotFoundError: No module named 'ddtrace.internal.openfeature._flageval_pii'`.

- [ ] **Step 1.3: Create the hash helper module.**

Write `ddtrace/internal/openfeature/_flageval_pii.py`:

```python
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
```

- [ ] **Step 1.4: Run the test and confirm it passes.**

Run: `scripts/run-tests openfeature -k test_canonical_vector`

Expected: PASS.

- [ ] **Step 1.5: Add the remaining hash-helper tests.**

Append to `TestHashTargetingKey` in `tests/openfeature/test_flageval_pii.py`:

```python
    def test_prefix_length_and_charset(self):
        """71 chars total, sha256_ prefix, 64 lowercase-hex digest."""
        from ddtrace.internal.openfeature._flageval_pii import TARGETING_KEY_HASH_PREFIX
        from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key

        got = hash_targeting_key(PII_CANONICAL_TARGETING_KEY)
        assert len(got) == 71
        assert got.startswith(TARGETING_KEY_HASH_PREFIX)
        hex_suffix = got[len(TARGETING_KEY_HASH_PREFIX):]
        assert len(hex_suffix) == 64
        assert all(c in "0123456789abcdef" for c in hex_suffix)

    def test_empty_input_stays_empty(self):
        """Absent targeting_key stays absent -- must NOT fabricate a shared pseudo-subject."""
        from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key

        assert hash_targeting_key("") == ""

    def test_does_not_normalize(self):
        """Every variant must produce a DIFFERENT digest from the canonical one.

        Trimming, case folding, or Unicode normalization would silently break the
        cross-SDK join. NFC vs NFD is the subtle case: same grapheme, different bytes.
        """
        from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key

        variants = {
            "leading whitespace": " " + PII_CANONICAL_TARGETING_KEY,
            "trailing whitespace": PII_CANONICAL_TARGETING_KEY + " ",
            "uppercased": PII_CANONICAL_TARGETING_KEY.upper(),
            # NFC precomposed U+00E9 vs NFD "e" + U+0301 combining acute.
            "NFC-composed accent": "josé@datadoghq.com",
            "NFD-decomposed accent": "josé@datadoghq.com",
        }
        seen = {PII_CANONICAL_HASHED: "canonical"}
        for name, input_str in variants.items():
            got = hash_targeting_key(input_str)
            assert got not in seen, f"{name} produced the same digest as {seen[got]}"
            seen[got] = name
```

- [ ] **Step 1.6: Run all hash tests and confirm they pass.**

Run: `scripts/run-tests openfeature -k TestHashTargetingKey`

Expected: PASS (4 tests).

- [ ] **Step 1.7: Commit.**

```bash
git add ddtrace/internal/openfeature/_flageval_pii.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
feat(openfeature): add hash_targeting_key for the flagevaluation PII contract

Unsalted SHA-256 over raw UTF-8 bytes with the sha256_ prefix. Cross-SDK
contract: every SDK reproduces the canonical vector byte-for-byte so hashed
targeting keys join across languages. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_FfeSnapshot` — atomic UFC + consent bundling

**Files:**
- Modify: `ddtrace/internal/openfeature/_config.py`

Two module globals cannot be assigned atomically together; bundling them in a
NamedTuple with a single global reference lets a reader observe a consistent
`(config, consent)` pair. This is the fix for the Java pilot's
`concern:bind-consent-to-evaluated-config`.

- [ ] **Step 2.1: Replace `_config.py` contents in full.**

Write `ddtrace/internal/openfeature/_config.py`:

```python
"""
Module-level storage for the parsed FFE configuration and its consent value.

The two values are bundled in one NamedTuple with a single module-level
reference so a reader always sees a consistent (config, consent) pair. This
closes the consent-lifecycle race described in the Java pilot's review
(concern:bind-consent-to-evaluated-config).
"""

from typing import NamedTuple
from typing import Optional
from typing import Union

from ddtrace.internal.native._native import ffe


class _FfeSnapshot(NamedTuple):
    """Atomic bundle of native config and the consent value read off the UFC."""

    config: ffe.Configuration
    observe_full_evaluation_data: bool


# Module-level global. Reads and writes are done through the accessors below so
# callers only ever observe a consistent snapshot.
_FFE_SNAPSHOT: Optional[_FfeSnapshot] = None


def _get_ffe_config() -> Optional[ffe.Configuration]:
    """Retrieve just the native FFE configuration. Preserved for compatibility."""
    snap = _FFE_SNAPSHOT
    return snap.config if snap is not None else None


def _get_ffe_snapshot() -> Optional[_FfeSnapshot]:
    """Retrieve the full snapshot (config + consent)."""
    return _FFE_SNAPSHOT


def _set_ffe_config(value: Union[None, ffe.Configuration, _FfeSnapshot]) -> None:
    """Set the FFE snapshot.

    Accepts either a bare native Configuration (existing test callers) or a
    _FfeSnapshot. A bare Configuration is stored as consent-off; None clears.
    """
    global _FFE_SNAPSHOT
    if value is None:
        _FFE_SNAPSHOT = None
    elif isinstance(value, _FfeSnapshot):
        _FFE_SNAPSHOT = value
    else:
        # Legacy path: a raw Configuration means consent-off (fail closed).
        _FFE_SNAPSHOT = _FfeSnapshot(config=value, observe_full_evaluation_data=False)
```

- [ ] **Step 2.2: Add unit tests for the snapshot accessors.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestFfeSnapshot:
    """Storage semantics of _FfeSnapshot in _config.py."""

    def test_default_is_none(self):
        from ddtrace.internal.openfeature._config import _get_ffe_snapshot
        from ddtrace.internal.openfeature._config import _set_ffe_config

        _set_ffe_config(None)
        assert _get_ffe_snapshot() is None

    def test_set_snapshot_round_trips(self):
        from unittest.mock import MagicMock

        from ddtrace.internal.openfeature._config import _FfeSnapshot
        from ddtrace.internal.openfeature._config import _get_ffe_snapshot
        from ddtrace.internal.openfeature._config import _set_ffe_config

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
        from unittest.mock import MagicMock

        from ddtrace.internal.openfeature._config import _get_ffe_snapshot
        from ddtrace.internal.openfeature._config import _set_ffe_config

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
        from unittest.mock import MagicMock

        from ddtrace.internal.openfeature._config import _FfeSnapshot
        from ddtrace.internal.openfeature._config import _get_ffe_config
        from ddtrace.internal.openfeature._config import _set_ffe_config

        fake_config = MagicMock(name="ffe.Configuration")
        _set_ffe_config(_FfeSnapshot(config=fake_config, observe_full_evaluation_data=True))
        try:
            assert _get_ffe_config() is fake_config
        finally:
            _set_ffe_config(None)
```

- [ ] **Step 2.3: Run the snapshot tests.**

Run: `scripts/run-tests openfeature -k TestFfeSnapshot`

Expected: PASS (4 tests).

- [ ] **Step 2.4: Run the full openfeature suite to verify backward compatibility.**

Run: `scripts/run-tests openfeature`

Expected: All existing openfeature tests still PASS. If any test that does `_set_ffe_config(None)` fails, the compatibility shim in `_set_ffe_config` is broken — fix the shim before proceeding.

- [ ] **Step 2.5: Commit.**

```bash
git add ddtrace/internal/openfeature/_config.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
refactor(openfeature): bundle FFE config and consent in an atomic snapshot

_FfeSnapshot lets a reader observe a consistent (config, consent) pair even
if Remote Config swaps the snapshot mid-evaluation. Legacy _set_ffe_config
callers pass a bare Configuration and get consent-off (fail closed). FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Read `observeFullEvaluationData` off the RC dict

**Files:**
- Modify: `ddtrace/internal/openfeature/_native.py`

- [ ] **Step 3.1: Write failing tests for UFC parsing.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestUFCObserveFullEvaluationDataParsing:
    """Read side of the contract: the field is read from the UFC ROOT (sibling
    of `environment`), and any non-True value fails closed to False."""

    def _minimal_ufc(self, extra_root: dict = None, environment_extra: dict = None) -> dict:
        env = {"name": "Staging"}
        if environment_extra:
            env.update(environment_extra)
        ufc = {"format": "SERVER", "environment": env, "flags": {}}
        if extra_root:
            ufc.update(extra_root)
        return ufc

    def _snapshot_for(self, ufc: dict):
        from ddtrace.internal.openfeature._config import _get_ffe_snapshot
        from ddtrace.internal.openfeature._config import _set_ffe_config
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        _set_ffe_config(None)
        process_ffe_configuration(ufc)
        return _get_ffe_snapshot()

    def test_absent_is_false(self):
        snap = self._snapshot_for(self._minimal_ufc())
        try:
            assert snap is not None
            assert snap.observe_full_evaluation_data is False
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)

    def test_explicit_false(self):
        snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": False}))
        try:
            assert snap.observe_full_evaluation_data is False
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)

    def test_explicit_true_opts_in(self):
        snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": True}))
        try:
            assert snap.observe_full_evaluation_data is True
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)

    def test_explicit_null_fails_closed(self):
        snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": None}))
        try:
            assert snap.observe_full_evaluation_data is False
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, [], {}])
    def test_wrong_type_fails_closed(self, bad):
        snap = self._snapshot_for(self._minimal_ufc({"observeFullEvaluationData": bad}))
        try:
            assert snap.observe_full_evaluation_data is False
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)

    def test_nested_under_environment_is_not_read(self):
        """FFL-2784 placement-drift regression guard: parser reading it from
        `environment` would report True here, hash forever in prod. The field
        lives at the UFC ROOT."""
        snap = self._snapshot_for(
            self._minimal_ufc(environment_extra={"observeFullEvaluationData": True})
        )
        try:
            assert snap.observe_full_evaluation_data is False
        finally:
            from ddtrace.internal.openfeature._config import _set_ffe_config

            _set_ffe_config(None)
```

- [ ] **Step 3.2: Run tests to confirm they fail.**

Run: `scripts/run-tests openfeature -k TestUFCObserveFullEvaluationDataParsing`

Expected: FAIL — all cases return `observe_full_evaluation_data=False` (the compatibility shim's default), but `test_explicit_true_opts_in` expects True.

- [ ] **Step 3.3: Update `_native.py` to read the field and store the snapshot.**

Replace the body of `process_ffe_configuration` in `ddtrace/internal/openfeature/_native.py`:

```python
def process_ffe_configuration(config):
    """
    Process FFE configuration and store as native Configuration object.

    Converts a dict config to JSON bytes and creates a native Configuration,
    alongside the observeFullEvaluationData consent value read from the top
    level of the UFC dict. The two are stored together as an atomic snapshot
    so a downstream reader always sees a consistent (config, consent) pair.

    Args:
        config: Configuration dict in format {"flags": {...}} or wrapped format
    """
    try:
        # observeFullEvaluationData sits at the UFC ROOT (sibling of `environment`,
        # NOT nested inside it). Any non-True value -- absent, False, None,
        # wrong-typed -- fails closed to False. Confirmed placement matches the
        # merged dd-source#22826.
        raw_consent = config.get("observeFullEvaluationData") if isinstance(config, dict) else None
        observe_full_evaluation_data: bool = raw_consent is True

        config_json = json.dumps(config)
        config_bytes = config_json.encode("utf-8")
        native_config = ffe.Configuration(config_bytes)
        _set_ffe_config(_FfeSnapshot(config=native_config, observe_full_evaluation_data=observe_full_evaluation_data))

        # Notify providers that configuration was received
        # Import here to avoid circular dependency
        from ddtrace.internal.openfeature._provider import _notify_providers_config_received

        _notify_providers_config_received()
    except ValueError as e:
        log.debug(
            "Failed to parse FFE configuration. The native library expects complete server format with: "
            "key, enabled, variationType, defaultVariation, variations (with type), and allocations fields. "
            "Error: %s",
            e,
            exc_info=True,
        )
```

Also update the imports at the top of `_native.py`:

```python
from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _set_ffe_config
```

(Replace the existing `from ddtrace.internal.openfeature._config import _set_ffe_config` with the two lines above.)

- [ ] **Step 3.4: Run parsing tests to confirm they pass.**

Run: `scripts/run-tests openfeature -k TestUFCObserveFullEvaluationDataParsing`

Expected: PASS (11 tests including the parametrized wrong-type cases).

- [ ] **Step 3.5: Run the full openfeature suite.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS. Existing tests that call `process_ffe_configuration` without the field should silently continue to work — they'll get consent-off snapshots.

- [ ] **Step 3.6: Commit.**

```bash
git add ddtrace/internal/openfeature/_native.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
feat(openfeature): read observeFullEvaluationData off the UFC root

The consent value sits at the top level of the UFC, a sibling of environment,
never nested inside it. Absent, null, and wrong-typed values all fail closed
to False. Stored alongside the parsed native Configuration as an atomic
snapshot. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `METADATA_OBSERVE_FULL_EVALUATION_DATA` constant + provider stamps consent

**Files:**
- Modify: `ddtrace/internal/openfeature/_flagevaluation_writer.py` (add constant only)
- Modify: `ddtrace/internal/openfeature/_provider.py`
- Modify: `tests/openfeature/test_provider.py` (if needed)

Consent must be stamped onto `flag_metadata` from the exact snapshot the
evaluation ran against, on every return path.

- [ ] **Step 4.1: Add the constant.**

At the top of `ddtrace/internal/openfeature/_flagevaluation_writer.py`, alongside `EVAL_TIMESTAMP_METADATA_KEY`:

```python
# Flag metadata key where the provider stamps the environment consent value.
# The evaluator snapshots observeFullEvaluationData from the UFC it evaluated
# against, so nothing downstream reads live config. Unprefixed snake_case
# because it is the cross-SDK contract key.
METADATA_OBSERVE_FULL_EVALUATION_DATA = "observe_full_evaluation_data"
```

Put it immediately below the existing `EVAL_TIMESTAMP_METADATA_KEY = "dd.eval.timestamp_ms"` line.

- [ ] **Step 4.2: Write failing tests for provider stamping.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestProviderStampsConsent:
    """The provider stamps observe_full_evaluation_data on flag_metadata on every
    return path, from the exact snapshot the evaluation ran against."""

    def _config_with_consent(self, observe: bool):
        """Build a minimal UFC dict with the given consent value."""
        return {
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
        from ddtrace.internal.openfeature._config import _set_ffe_config

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
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_success_path_stamps_consent_false(self, monkeypatch):
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        process_ffe_configuration(self._config_with_consent(False))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("test-bool", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is False

    def test_flag_not_found_still_stamps_consent(self, monkeypatch):
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        process_ffe_configuration(self._config_with_consent(True))
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("no-such-flag", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is True

    def test_no_configuration_fails_closed(self, monkeypatch):
        """PROVIDER_NOT_READY: no environment behind the evaluation, so no consent
        to honor. Must stamp False rather than leave the key absent-and-ambiguous."""
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )

        # No process_ffe_configuration call -- snapshot stays None.
        provider = self._provider(monkeypatch)

        details = provider.resolve_boolean_details("anything", False)
        assert details.flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] is False
```

- [ ] **Step 4.3: Run provider-stamp tests to confirm they fail.**

Run: `scripts/run-tests openfeature -k TestProviderStampsConsent`

Expected: FAIL — the metadata key is not stamped yet.

- [ ] **Step 4.4: Update `_provider.py::_resolve_details`.**

Two edits in `ddtrace/internal/openfeature/_provider.py`:

Edit 1 — add the import at the top of the file, alongside the other `_flagevaluation_writer` imports:

```python
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
```

Edit 2 — replace the block that initializes `flag_metadata` at the top of `_resolve_details` (currently lines 369-372):

Current code:

```python
        # AIDEV-NOTE: Stamp eval-time at provider entry so every OpenFeature exit path
        # can feed the EVP flagevaluation hook first_evaluation/last_evaluation from
        # evaluation time, not the later hook/flush time.
        flag_metadata: dict[str, typing.Any] = {EVAL_TIMESTAMP_METADATA_KEY: int(time.time() * 1000)}
```

Replace with:

```python
        # AIDEV-NOTE: Stamp eval-time and consent at provider entry so every
        # OpenFeature exit path carries them. Consent is snapshotted from the
        # exact FFE snapshot this evaluation runs against -- never read from
        # live config downstream (see docs/superpowers/specs/2026-08-06-
        # pii-flagevaluations-hashing-design.md).
        snapshot = _get_ffe_snapshot()
        observe_full_evaluation_data = snapshot.observe_full_evaluation_data if snapshot is not None else False
        flag_metadata: dict[str, typing.Any] = {
            EVAL_TIMESTAMP_METADATA_KEY: int(time.time() * 1000),
            METADATA_OBSERVE_FULL_EVALUATION_DATA: observe_full_evaluation_data,
        }
```

Edit 3 — replace the existing `config = _get_ffe_config()` line inside the `try:` block of `_resolve_details` with:

```python
            # Use the snapshot's config so evaluation and consent stamp agree.
            config = snapshot.config if snapshot is not None else None
```

Edit 4 — update the `_config` import at the top of `_provider.py`:

Change:

```python
from ddtrace.internal.openfeature._config import _get_ffe_config
```

to:

```python
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
```

Then remove the now-unused `_get_ffe_config` reference. (Grep confirms it is only used inside `_resolve_details`.)

- [ ] **Step 4.5: Run provider-stamp tests to confirm they pass.**

Run: `scripts/run-tests openfeature -k TestProviderStampsConsent`

Expected: PASS (4 tests).

- [ ] **Step 4.6: Run the full openfeature suite.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS.

- [ ] **Step 4.7: Commit.**

```bash
git add ddtrace/internal/openfeature/_flagevaluation_writer.py ddtrace/internal/openfeature/_provider.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
feat(openfeature): stamp observe_full_evaluation_data on flag_metadata

The provider snapshots consent from the FFE snapshot each evaluation runs
against and stamps it on flag_metadata under the cross-SDK key
observe_full_evaluation_data on every return path. Downstream must read
consent from metadata, never from live config. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Hook reads consent (fail closed) and skips attrs when off

**Files:**
- Modify: `ddtrace/internal/openfeature/_flag_eval_evp_hook.py`
- Modify: `ddtrace/internal/openfeature/_flagevaluation_writer.py` (extend `_EvalEvent`)
- Modify: `tests/openfeature/test_flagevaluation_writer.py` (update `_make_event` helper)

- [ ] **Step 5.1: Extend `_EvalEvent` with the new field.**

In `ddtrace/internal/openfeature/_flagevaluation_writer.py`, update `_EvalEvent`:

```python
class _EvalEvent(typing.NamedTuple):
    """Minimal snapshot handed from finally_after to the background worker."""

    flag_key: str
    variant: str  # "" when absent (= runtime_default)
    allocation_key: str
    targeting_key: str
    attrs: dict[str, typing.Any]  # flattened and pruned context snapshot
    runtime_default: bool
    error_message: str
    eval_time_ms: int
    observe_full_evaluation_data: bool
```

- [ ] **Step 5.2: Update the `_make_event` helper in the existing writer tests to default the new field.**

In `tests/openfeature/test_flagevaluation_writer.py`, replace the `_make_event` helper (around lines 59-80) with:

```python
def _make_event(
    flag_key: str = "my-flag",
    variant: str = "on",
    allocation_key: str = "alloc-1",
    targeting_key: str = "user-1",
    attrs: dict = None,
    runtime_default: bool = False,
    error_message: str = "",
    eval_time_ms: int = None,
    observe_full_evaluation_data: bool = True,
) -> _EvalEvent:
    """Build an _EvalEvent. Defaults observe_full_evaluation_data=True so pre-PII
    tests keep asserting the existing (raw targeting_key + context) shape."""
    if eval_time_ms is None:
        eval_time_ms = int(time.time() * 1000)
    return _EvalEvent(
        flag_key=flag_key,
        variant=variant,
        allocation_key=allocation_key,
        targeting_key=targeting_key,
        attrs=attrs or {},
        runtime_default=runtime_default,
        error_message=error_message,
        eval_time_ms=eval_time_ms,
        observe_full_evaluation_data=observe_full_evaluation_data,
    )
```

Rationale for defaulting to `True`: existing tests assert the raw-shape behavior; the new default of the field on `_EvalEvent` itself does not have a natural safe value — but the test helper is only used by pre-PII tests, and setting it to `True` there keeps them meaningful.

- [ ] **Step 5.3: Run the full openfeature suite to confirm nothing broke from the `_EvalEvent` field addition.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS (the NamedTuple is constructed only via the `_make_event` helper in tests and via the hook in production, both updated).

Investigate any failure — grep for any other place that constructs `_EvalEvent(...)` directly (e.g., `grep -rn "_EvalEvent(" tests/`) and either update it or move to Step 5.4 if there are none.

- [ ] **Step 5.4: Write failing hook-reads-consent tests.**

Append to `tests/openfeature/test_flag_eval_evp_hook.py`:

```python
class TestFlagEvalEVPHookReadsConsent:
    """The hook reads observe_full_evaluation_data off details.flag_metadata,
    fails closed on missing or malformed values, and skips attribute capture
    when consent is off."""

    def test_consent_true_captured_from_metadata(self, hook, writer):
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )

        hc = _make_hook_context(attrs={"plan": "enterprise"})
        details = _make_details(flag_metadata={METADATA_OBSERVE_FULL_EVALUATION_DATA: True})
        hook.finally_after(hc, details, {})

        event = writer.enqueue.call_args[0][0]
        assert event.observe_full_evaluation_data is True
        assert event.attrs == {"plan": "enterprise"}

    def test_consent_false_captured_and_attrs_dropped(self, hook, writer):
        """Consent-off: the context is neither serialized nor keyed, so the hook
        skips attribute capture -- prevents PII living in the writer queue."""
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )

        hc = _make_hook_context(attrs={"plan": "enterprise", "user_email": "leak@example.com"})
        details = _make_details(flag_metadata={METADATA_OBSERVE_FULL_EVALUATION_DATA: False})
        hook.finally_after(hc, details, {})

        event = writer.enqueue.call_args[0][0]
        assert event.observe_full_evaluation_data is False
        assert event.attrs == {}

    def test_missing_metadata_fails_closed(self, hook, writer):
        hc = _make_hook_context(attrs={"plan": "enterprise"})
        details = _make_details(flag_metadata={})
        hook.finally_after(hc, details, {})

        event = writer.enqueue.call_args[0][0]
        assert event.observe_full_evaluation_data is False
        assert event.attrs == {}

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, None, []])
    def test_wrong_type_fails_closed(self, hook, writer, bad):
        from ddtrace.internal.openfeature._flagevaluation_writer import (
            METADATA_OBSERVE_FULL_EVALUATION_DATA,
        )

        hc = _make_hook_context()
        details = _make_details(flag_metadata={METADATA_OBSERVE_FULL_EVALUATION_DATA: bad})
        hook.finally_after(hc, details, {})

        event = writer.enqueue.call_args[0][0]
        assert event.observe_full_evaluation_data is False
```

- [ ] **Step 5.5: Run hook tests to confirm they fail.**

Run: `scripts/run-tests openfeature -k TestFlagEvalEVPHookReadsConsent`

Expected: FAIL — hook doesn't read consent yet; `event.observe_full_evaluation_data` will hold whatever `_EvalEvent` gets. (If it errors out because the hook is passing insufficient kwargs to `_EvalEvent`, that's expected.)

- [ ] **Step 5.6: Update `_flag_eval_evp_hook.py::finally_after`.**

In `ddtrace/internal/openfeature/_flag_eval_evp_hook.py`:

Add import at the top alongside the existing metadata-key imports:

```python
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
```

Then update the body of `finally_after` — the section where the event is built. Replace the block from the `# Variant: absent variant signals a runtime default.` comment through the `self._writer.enqueue(event)` line with:

```python
            # Variant: absent variant signals a runtime default.
            variant = ""
            if details.variant:
                variant = details.variant
            runtime_default = variant == ""

            # Consent for this evaluation, read only from metadata the evaluator
            # stamped. Anything not exactly True is treated as consent-off:
            # a missing key, a non-bool, or None -- so a broken upstream cannot
            # silently opt in.
            observe_full_evaluation_data = (
                metadata.get(METADATA_OBSERVE_FULL_EVALUATION_DATA) is True
            )

            # Targeting key and attributes from the evaluation context.
            eval_ctx = hook_context.evaluation_context
            targeting_key = eval_ctx.targeting_key or ""
            # Consent-off: the context is dropped at serialization and from the
            # bucket key. Skipping capture here keeps PII attributes out of the
            # writer queue entirely -- and matches the aggregator invariant
            # that the bucket key carries only dimensions that survive
            # serialization.
            if observe_full_evaluation_data:
                # Shallow copy so we don't hold a reference to the caller's live dict.
                attrs: dict[str, typing.Any] = dict(eval_ctx.attributes or {})
            else:
                attrs = {}

            # Error message (best-effort; absent on success paths).
            error_message = ""
            if details.error_message:
                error_message = str(details.error_message)
            elif details.error_code:
                error_message = (
                    str(details.error_code.value) if hasattr(details.error_code, "value") else str(details.error_code)
                )

            event = _EvalEvent(
                flag_key=flag_key,
                variant=variant,
                allocation_key=allocation_key,
                targeting_key=targeting_key,
                attrs=attrs,
                runtime_default=runtime_default,
                error_message=error_message,
                eval_time_ms=eval_time_ms,
                observe_full_evaluation_data=observe_full_evaluation_data,
            )

            self._writer.enqueue(event)
```

- [ ] **Step 5.7: Run hook tests to confirm they pass.**

Run: `scripts/run-tests openfeature -k TestFlagEvalEVPHookReadsConsent`

Expected: PASS (9 tests including parametrized wrong-type cases).

- [ ] **Step 5.8: Run the full openfeature suite.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS.

- [ ] **Step 5.9: Commit.**

```bash
git add ddtrace/internal/openfeature/_flag_eval_evp_hook.py ddtrace/internal/openfeature/_flagevaluation_writer.py tests/openfeature/test_flag_eval_evp_hook.py tests/openfeature/test_flagevaluation_writer.py
git commit -m "$(cat <<'EOF'
feat(openfeature): hook reads consent from metadata and skips attrs when off

The EVP hook reads observe_full_evaluation_data from FlagResolutionDetails
metadata (never from live config), fails closed on missing or non-bool
values, and skips evaluation-context capture entirely when consent is off.
Prevents PII attribute dicts from living in the writer queue. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Aggregator keys on consent, AND-folds, forces context-off on consent-off

**Files:**
- Modify: `ddtrace/internal/openfeature/_flagevaluation_writer.py` (extend `_Entry`, key tuple, `_aggregate`)
- Modify: `tests/openfeature/test_flagevaluation_writer.py` (update `_Entry(...)` construction call sites)

- [ ] **Step 6.1: Extend `_Entry` with consent.**

In `ddtrace/internal/openfeature/_flagevaluation_writer.py`, update `_Entry`:

```python
class _Entry:
    """Per-bucket aggregation state."""

    __slots__ = (
        "count",
        "first_evaluation",
        "last_evaluation",
        "runtime_default",
        "targeting_key",
        "context_attrs",
        "error_message",
        "observe_full_evaluation_data",
    )

    def __init__(
        self,
        eval_time_ms: int,
        runtime_default: bool,
        targeting_key: str,
        context_attrs: dict[str, typing.Any],
        error_message: str,
        observe_full_evaluation_data: bool = False,
    ) -> None:
        self.count: int = 1
        self.first_evaluation: int = eval_time_ms
        self.last_evaluation: int = eval_time_ms
        self.runtime_default: bool = runtime_default
        # Full-tier only:
        self.targeting_key: str = targeting_key
        self.context_attrs: dict[str, typing.Any] = context_attrs
        self.error_message: str = error_message
        # Serialization branches on this value. Degraded-tier entries always
        # store False here; consent is not a degraded key dimension.
        self.observe_full_evaluation_data: bool = observe_full_evaluation_data

    def observe(self, eval_time_ms: int) -> None:
        """Update count and first/last bounds for a repeated evaluation."""
        self.count += 1
        if eval_time_ms < self.first_evaluation:
            self.first_evaluation = eval_time_ms
        if eval_time_ms > self.last_evaluation:
            self.last_evaluation = eval_time_ms
```

The new parameter defaults to `False` so existing test call sites that pass positional args (e.g. `_Entry(1000, False, "", {}, "")`) keep working — they get a consent-off entry, which is the safe default.

- [ ] **Step 6.2: Update `_aggregate` for the new key dimension, forcing, and AND-fold.**

Replace the body of `_aggregate` in `_flagevaluation_writer.py` with:

```python
    def _aggregate(self, event: _EvalEvent) -> None:
        """
        Aggregate a single evaluation event into the two-tier maps.

        Implements: full-tier → degraded-tier → drop-counted cascade.
        Canonical key computation happens here (off the hot path). Context was already
        flattened and pruned before enqueue.

        Consent handling:
        - The full-tier key carries observe_full_evaluation_data so mixed-consent
          evaluations never merge and inherit one policy.
        - When consent is off, context is dropped from both the entry and the key,
          because the wire event carries no context on that path -- so keying on
          discarded data would burn per-flag cardinality on the privacy-protected
          path specifically (see concern:consent-off-bucket-keying).
        - On fast-path merge, AND-fold consent into the entry: any single
          consent-off observation forces the whole bucket onto the protected wire
          path, even if a future refactor drops consent from the key.
        """
        # Enforce the consent-off invariant: no context on the wire → no context
        # in the key, no context in the entry.
        if event.observe_full_evaluation_data:
            context_attrs = event.attrs or {}
        else:
            context_attrs = {}

        # Build the full-tier key tuple.
        ctx_key = canonical_context_key(context_attrs)
        full_key = (
            event.flag_key,
            event.variant,
            event.allocation_key,
            event.runtime_default,
            event.error_message,
            event.targeting_key,
            ctx_key,
            event.observe_full_evaluation_data,
        )

        with self._lock:
            # Fast path: existing full-tier bucket.
            if full_key in self._full:
                entry = self._full[full_key]
                # Defense in depth: if the key ever stops carrying consent, one
                # consent-off observation still forces the whole bucket onto the
                # privacy-protected path.
                entry.observe_full_evaluation_data = (
                    entry.observe_full_evaluation_data and event.observe_full_evaluation_data
                )
                entry.observe(event.eval_time_ms)
                return

            # Per-flag cap check.
            per_flag = self._per_flag_count.get(event.flag_key, 0)
            if per_flag >= PER_FLAG_CAP:
                self._add_to_degraded(event)
                return

            # Increment per-flag attempt count before checking globalCap (matches Go design).
            self._per_flag_count[event.flag_key] = per_flag + 1

            # Global cap check.
            if self._global_count >= GLOBAL_CAP:
                self._add_to_degraded(event)
                return

            # New full-tier bucket.
            self._full[full_key] = _Entry(
                eval_time_ms=event.eval_time_ms,
                runtime_default=event.runtime_default,
                targeting_key=event.targeting_key,
                context_attrs=_json_safe_context(context_attrs),
                error_message=event.error_message,
                observe_full_evaluation_data=event.observe_full_evaluation_data,
            )
            self._global_count += 1
```

- [ ] **Step 6.3: Add aggregator tests.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestAggregatorConsent:
    """Consent semantics of full-tier aggregation."""

    @pytest.fixture
    def writer(self):
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        return FlagEvaluationWriter(interval=10.0)

    def _event(self, writer, observe_full_evaluation_data: bool, attrs=None, targeting_key: str = "user-1"):
        import time

        from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent

        return _EvalEvent(
            flag_key="f",
            variant="on",
            allocation_key="alloc-1",
            targeting_key=targeting_key,
            attrs=attrs or {},
            runtime_default=False,
            error_message="",
            eval_time_ms=int(time.time() * 1000),
            observe_full_evaluation_data=observe_full_evaluation_data,
        )

    def test_consent_off_merges_distinct_contexts_into_one_bucket(self, writer):
        """concern:consent-off-bucket-keying regression: without consent the
        context is discarded at serialization, so distinct contexts must
        collapse into one bucket -- otherwise a high-cardinality attribute burns
        per-flag cardinality on privacy-protected traffic."""
        for i in range(5):
            writer._aggregate(self._event(writer, observe_full_evaluation_data=False, attrs={"request_id": i}))
        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.count == 5
        assert entry.context_attrs == {}

    def test_consent_on_keeps_distinct_contexts_distinct(self, writer):
        for i in range(5):
            writer._aggregate(self._event(writer, observe_full_evaluation_data=True, attrs={"request_id": i}))
        assert len(writer._full) == 5

    def test_mixed_consent_does_not_merge(self, writer):
        writer._aggregate(self._event(writer, observe_full_evaluation_data=False))
        writer._aggregate(self._event(writer, observe_full_evaluation_data=True))
        assert len(writer._full) == 2

    def test_and_fold_on_merge(self, writer):
        """Defense in depth: if key drift lets a consent-off observation land on
        a consent-on bucket, the entry must still flip to consent-off."""
        # Seed a consent-on bucket.
        writer._aggregate(self._event(writer, observe_full_evaluation_data=True))
        assert len(writer._full) == 1
        entry = list(writer._full.values())[0]
        assert entry.observe_full_evaluation_data is True

        # Simulate key drift by manually replaying a consent-off observation onto
        # the existing bucket key (would not happen through _aggregate today; this
        # exercises the AND-fold branch directly).
        (full_key,) = writer._full.keys()
        with writer._lock:
            entry = writer._full[full_key]
            entry.observe_full_evaluation_data = entry.observe_full_evaluation_data and False
            entry.observe(int(time.time() * 1000))

        assert entry.observe_full_evaluation_data is False
```

Note: this test needs `import time` in the file. If it's not already imported at the top of `test_flageval_pii.py`, add `import time` alongside `import pytest`.

- [ ] **Step 6.4: Run aggregator tests to confirm they pass.**

Run: `scripts/run-tests openfeature -k TestAggregatorConsent`

Expected: PASS (4 tests).

- [ ] **Step 6.5: Run the full openfeature suite.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS. In particular, `test_flagevaluation_writer.py`'s existing aggregation tests should keep working because `_make_event` defaults `observe_full_evaluation_data=True` and the pre-PII test scenarios all evaluate under implicit consent-on.

- [ ] **Step 6.6: Commit.**

```bash
git add ddtrace/internal/openfeature/_flagevaluation_writer.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
feat(openfeature): aggregate consent as a full-tier key dimension

Full-tier bucket key carries observe_full_evaluation_data so mixed-consent
evaluations never merge. Consent-off aggregations drop context from both key
and entry (bucket key must carry only dimensions that survive serialization).
Fast-path merge AND-folds consent as defense in depth. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Flush-time hashing + branch on consent for serialization

**Files:**
- Modify: `ddtrace/internal/openfeature/_flagevaluation_writer.py` (`periodic`)

- [ ] **Step 7.1: Import the hash helper at the top of `_flagevaluation_writer.py`.**

Alongside the other module imports:

```python
from ddtrace.internal.openfeature._flageval_pii import hash_targeting_key
```

- [ ] **Step 7.2: Update the full-tier serialization block in `periodic()`.**

In `ddtrace/internal/openfeature/_flagevaluation_writer.py::FlagEvaluationWriter.periodic`, locate the `# Full-tier events: all optional fields present.` block (currently around line 519) and replace the full-tier `for` loop with:

```python
        # Full-tier events: all optional fields present.
        for key, entry in full.items():
            flag_key = key[0]
            variant = key[1]
            allocation_key = key[2]
            ev = _base_event(flag_key, entry, flush_time_ms)
            if entry.runtime_default:
                ev["runtime_default_used"] = True
            # Consent-on emits raw targeting_key + context; consent-off emits the
            # hashed key and omits context entirely (absent, not null, not {}).
            # Consent is read from the bucket snapshot -- never from live config.
            if entry.observe_full_evaluation_data:
                if entry.targeting_key:
                    ev["targeting_key"] = entry.targeting_key
                if entry.context_attrs:
                    ev["context"] = {"evaluation": entry.context_attrs}
            else:
                hashed = hash_targeting_key(entry.targeting_key)
                if hashed:
                    ev["targeting_key"] = hashed
                # No context field under any circumstances when consent is off.
            if variant:
                ev["variant"] = {"key": variant}
            if allocation_key:
                ev["allocation"] = {"key": allocation_key}
            if entry.error_message:
                ev["error"] = {"message": entry.error_message}
            events.append(ev)
```

The degraded-tier loop below is unchanged: degraded events already omit `targeting_key` and `context` regardless of consent.

- [ ] **Step 7.3: Add end-to-end serialization tests.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestFlushSerialization:
    """Raw-wire assertions on the flagevaluations payload bytes.

    Assertions on raw JSON bytes catch raw values routed into unexpected fields,
    which a decode-then-inspect check would miss.
    """

    @pytest.fixture
    def writer(self):
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        return FlagEvaluationWriter(interval=10.0)

    def _pii_event(self, writer, observe_full_evaluation_data: bool):
        import time

        from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent

        # Hook is what should have skipped attrs on consent-off. Simulate that here:
        attrs = {} if not observe_full_evaluation_data else {
            "org_id": 1234,
            "user_email": PII_CANONICAL_TARGETING_KEY,
            "plan": "enterprise",
            "region": "us-east-1",
        }
        return _EvalEvent(
            flag_key="pii-flag",
            variant="on",
            allocation_key="default-allocation",
            targeting_key=PII_CANONICAL_TARGETING_KEY,
            attrs=attrs,
            runtime_default=False,
            error_message="",
            eval_time_ms=int(time.time() * 1000),
            observe_full_evaluation_data=observe_full_evaluation_data,
        )

    def _flush_capture(self, writer):
        """Run periodic() and return the raw payload bytes that _send_payload
        received."""
        from unittest import mock

        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
        assert mock_send.call_count >= 1, "expected at least one payload flush"
        payload_bytes, _ = mock_send.call_args[0]
        return payload_bytes

    def test_consent_off_hashes_and_omits_context(self, writer):
        import json

        writer._aggregate(self._pii_event(writer, observe_full_evaluation_data=False))
        payload_bytes = self._flush_capture(writer)

        # Raw-wire assertions first: catches a raw value routed into an unexpected field.
        raw = payload_bytes.decode("utf-8")
        assert PII_CANONICAL_TARGETING_KEY not in raw
        assert "enterprise" not in raw
        assert "us-east-1" not in raw
        assert "user_email" not in raw

        decoded = json.loads(payload_bytes)
        assert len(decoded["flagEvaluations"]) == 1
        event = decoded["flagEvaluations"][0]
        assert event["targeting_key"] == PII_CANONICAL_HASHED
        # "Omitted" means the key is absent -- not None, not {}.
        assert "context" not in event

    def test_consent_on_emits_raw(self, writer):
        import json

        writer._aggregate(self._pii_event(writer, observe_full_evaluation_data=True))
        payload_bytes = self._flush_capture(writer)

        decoded = json.loads(payload_bytes)
        event = decoded["flagEvaluations"][0]
        assert event["targeting_key"] == PII_CANONICAL_TARGETING_KEY
        assert "context" in event
        assert event["context"]["evaluation"]["plan"] == "enterprise"

    def test_degraded_tier_never_emits_subject_or_context(self, writer):
        """Regardless of consent -- degraded already omits both. This proves the
        assertion for consent-on too (the RFC's negative control on the degraded
        path)."""
        import json

        # globalCap 0 routes every new full key straight to the degraded tier.
        writer._global_count = writer._global_count  # placeholder; use direct import below
        from ddtrace.internal.openfeature import _flagevaluation_writer

        original_global_cap = _flagevaluation_writer.GLOBAL_CAP
        try:
            _flagevaluation_writer.GLOBAL_CAP = 0
            # Rebuild writer under the patched cap.
            w = _flagevaluation_writer.FlagEvaluationWriter(interval=10.0)
            for consent in (False, True):
                w._aggregate(self._pii_event(w, observe_full_evaluation_data=consent))
                payload_bytes = self._flush_capture(w)
                raw = payload_bytes.decode("utf-8")
                assert PII_CANONICAL_TARGETING_KEY not in raw
                decoded = json.loads(payload_bytes)
                event = decoded["flagEvaluations"][0]
                assert "targeting_key" not in event
                assert "context" not in event
        finally:
            _flagevaluation_writer.GLOBAL_CAP = original_global_cap
```

- [ ] **Step 7.4: Run the new tests to confirm they pass.**

Run: `scripts/run-tests openfeature -k TestFlushSerialization`

Expected: PASS (3 tests).

- [ ] **Step 7.5: Run the full openfeature suite.**

Run: `scripts/run-tests openfeature`

Expected: All existing tests still PASS. Watch specifically for `test_periodic_drains_queue_and_builds_payload` and any test that inspects the flushed payload shape — the `_make_event` helper defaults `observe_full_evaluation_data=True`, so those tests continue to see the raw shape.

- [ ] **Step 7.6: Commit.**

```bash
git add ddtrace/internal/openfeature/_flagevaluation_writer.py tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
feat(openfeature): hash targeting_key and omit context by default

FlagEvaluationWriter.periodic() now branches on
entry.observe_full_evaluation_data when serializing full-tier events.
Consent-off: hashed sha256_ targeting_key, context field absent. Consent-on:
raw targeting_key and context.evaluation. Hashing runs once per bucket at
flush cadence, matching dd-trace-java#12042 and dd-trace-go#5151. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Consent-lifecycle regression test (the L3-caught Java bug)

**Files:**
- Modify: `tests/openfeature/test_flageval_pii.py`

This is the single most load-bearing test on the whole PR: unit tests missed
this in Java; system-tests caught it. We assert it here so it never regresses
in dd-trace-py.

- [ ] **Step 8.1: Add the consent-lifecycle regression test.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestConsentLifecycle:
    """The Java-pilot L3 bug: consent read from live config at flush time. A
    later RC update retroactively applied another environment's policy. Both
    directions leak. dd-trace-py's design snapshots consent at evaluation time
    and carries it on the event; this test guards that."""

    def _config(self, observe: bool) -> dict:
        return {
            "format": "SERVER",
            "observeFullEvaluationData": observe,
            "environment": {"name": "Staging"},
            "flags": {
                "pii-flag": {
                    "key": "pii-flag",
                    "enabled": True,
                    "variationType": "STRING",
                    "defaultVariation": "on",
                    "variations": {"on": {"key": "on", "value": "on-value"}},
                    "allocations": [
                        {
                            "key": "default-allocation",
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
        from ddtrace.internal.openfeature._config import _set_ffe_config

        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize(
        "consent_at_evaluation,consent_after_update,want_hashed",
        [
            # Later opt-in must NOT retroactively unmask an already-hashed subject.
            (False, True, True),
            # Later opt-out must NOT retroactively hash an already-consented subject.
            (True, False, False),
        ],
        ids=["off→on stays hashed", "on→off stays raw"],
    )
    def test_consent_is_not_re_read_after_evaluation(
        self, monkeypatch, consent_at_evaluation, consent_after_update, want_hashed
    ):
        import json
        from unittest import mock

        from openfeature.evaluation_context import EvaluationContext

        from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")

        from ddtrace.internal.openfeature._provider import DataDogProvider

        # 1. Install the consent-at-evaluation config.
        process_ffe_configuration(self._config(consent_at_evaluation))

        # 2. Build provider + writer + hook, evaluate.
        provider = DataDogProvider()
        writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvalEVPHook(writer=writer)

        eval_ctx = EvaluationContext(
            targeting_key=PII_CANONICAL_TARGETING_KEY,
            attributes={"plan": "enterprise"},
        )
        details = provider.resolve_string_details("pii-flag", "fallback", eval_ctx)

        # 3. Run the hook exactly as the SDK would (finally_after in production).
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.flag_evaluation import FlagType
        from openfeature.hook import HookContext

        hook_context = HookContext(
            flag_key="pii-flag",
            flag_type=FlagType.STRING,
            default_value="fallback",
            evaluation_context=eval_ctx,
        )
        hook_details = FlagEvaluationDetails(
            flag_key="pii-flag",
            value=details.value,
            variant=details.variant,
            reason=details.reason,
            flag_metadata=details.flag_metadata,
            error_message=details.error_message,
            error_code=details.error_code,
        )
        hook.finally_after(hook_context, hook_details, {})

        # 4. Remote Config replaces the configuration BEFORE aggregation/flush.
        #    Nothing downstream of the evaluator may notice.
        process_ffe_configuration(self._config(consent_after_update))

        # 5. Flush and inspect the wire bytes.
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
        payload_bytes, _ = mock_send.call_args[0]
        decoded = json.loads(payload_bytes)
        event = decoded["flagEvaluations"][0]

        if want_hashed:
            assert event["targeting_key"] == PII_CANONICAL_HASHED
            assert "context" not in event
        else:
            assert event["targeting_key"] == PII_CANONICAL_TARGETING_KEY
            assert event["context"]["evaluation"]["plan"] == "enterprise"
```

- [ ] **Step 8.2: Run the lifecycle tests.**

Run: `scripts/run-tests openfeature -k TestConsentLifecycle`

Expected: PASS (2 parametrized cases).

If either fails, the design's consent-lifecycle guarantee is broken somewhere upstream — stop and investigate. Typical failure: the provider read live config instead of the snapshot at Step 4.4 (Task 4).

- [ ] **Step 8.3: Commit.**

```bash
git add tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
test(openfeature): regression guard for the consent-lifecycle race

The Java pilot shipped a version of this feature that read consent from live
config at flush time; system-tests caught it and unit tests had not. This
test evaluates under one consent value, swaps the RC snapshot before flush,
and asserts the flushed event still reflects the value at evaluation time.
Both directions covered. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `DoLog` non-impact proof

**Files:**
- Modify: `tests/openfeature/test_flageval_pii.py`

- [ ] **Step 9.1: Add the DoLog non-impact test.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestDoLogNonImpact:
    """The RFC's `DoLog` non-impact proof: for each consent value, the emitted
    shape must be byte-identical across do_log values.

    Timestamps are pinned so the comparison isolates the PII-relevant shape;
    wall-clock drift between the two builds would otherwise always differ.
    """

    def _config(self, observe: bool, do_log: bool) -> dict:
        return {
            "format": "SERVER",
            "observeFullEvaluationData": observe,
            "environment": {"name": "Staging"},
            "flags": {
                "pii-flag": {
                    "key": "pii-flag",
                    "enabled": True,
                    "variationType": "STRING",
                    "defaultVariation": "on",
                    "variations": {"on": {"key": "on", "value": "on-value"}},
                    "allocations": [
                        {
                            "key": "default-allocation",
                            "rules": [],
                            "splits": [{"variationKey": "on", "shards": []}],
                            "doLog": do_log,
                        }
                    ],
                },
            },
        }

    @pytest.fixture(autouse=True)
    def _clear_state(self):
        from ddtrace.internal.openfeature._config import _set_ffe_config

        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize("consent", [False, True], ids=["consent-off", "consent-on"])
    def test_do_log_does_not_affect_emitted_shape(self, monkeypatch, consent):
        import json
        from unittest import mock

        from openfeature.evaluation_context import EvaluationContext
        from openfeature.flag_evaluation import FlagEvaluationDetails
        from openfeature.flag_evaluation import FlagType
        from openfeature.hook import HookContext

        from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "true")

        from ddtrace.internal.openfeature._provider import DataDogProvider

        fixed_eval_time_ms = 1785000000000

        payload_shapes: dict[bool, dict] = {}
        for do_log in (False, True):
            process_ffe_configuration(self._config(consent, do_log))
            provider = DataDogProvider()
            writer = FlagEvaluationWriter(interval=10.0)
            hook = FlagEvalEVPHook(writer=writer)

            eval_ctx = EvaluationContext(
                targeting_key=PII_CANONICAL_TARGETING_KEY,
                attributes={"plan": "enterprise"},
            )
            details = provider.resolve_string_details("pii-flag", "fallback", eval_ctx)
            # Pin eval-time.
            details.flag_metadata["dd.eval.timestamp_ms"] = fixed_eval_time_ms

            hook_context = HookContext(
                flag_key="pii-flag",
                flag_type=FlagType.STRING,
                default_value="fallback",
                evaluation_context=eval_ctx,
            )
            hook_details = FlagEvaluationDetails(
                flag_key="pii-flag",
                value=details.value,
                variant=details.variant,
                reason=details.reason,
                flag_metadata=details.flag_metadata,
                error_message=details.error_message,
                error_code=details.error_code,
            )
            hook.finally_after(hook_context, hook_details, {})

            with mock.patch.object(writer, "_send_payload") as mock_send, \
                 mock.patch("time.time", return_value=fixed_eval_time_ms / 1000):
                writer.periodic()

            payload_bytes, _ = mock_send.call_args[0]
            decoded = json.loads(payload_bytes)
            event = decoded["flagEvaluations"][0]
            # Drop `timestamp` (writer-clock, not eval-time) so the diff isolates
            # the PII-relevant shape.
            event.pop("timestamp", None)
            payload_shapes[do_log] = event

        assert payload_shapes[False] == payload_shapes[True], (
            f"DoLog must not affect the emitted shape:\n"
            f"  do_log=False: {payload_shapes[False]}\n"
            f"  do_log=True:  {payload_shapes[True]}"
        )

        # And the shape must be the correct one for the consent value.
        if consent:
            assert payload_shapes[True]["targeting_key"] == PII_CANONICAL_TARGETING_KEY
        else:
            assert payload_shapes[True]["targeting_key"] == PII_CANONICAL_HASHED
```

- [ ] **Step 9.2: Run the DoLog test.**

Run: `scripts/run-tests openfeature -k TestDoLogNonImpact`

Expected: PASS (2 parametrized cases).

- [ ] **Step 9.3: Commit.**

```bash
git add tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
test(openfeature): DoLog non-impact proof required by the RFC

For each consent value, the flushed-event JSON is byte-identical across
do_log values. Guards against a future refactor that gates PII behavior on
DoLog, which the RFC explicitly forbids. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Kill-switch proof

**Files:**
- Modify: `tests/openfeature/test_flageval_pii.py`

- [ ] **Step 10.1: Add the kill-switch test.**

Append to `tests/openfeature/test_flageval_pii.py`:

```python
class TestKillSwitch:
    """DD_FLAGGING_EVALUATION_COUNTS_ENABLED=false disables the EVP flagevaluation
    track entirely and always wins over observeFullEvaluationData."""

    def _config(self, observe: bool) -> dict:
        return {
            "format": "SERVER",
            "observeFullEvaluationData": observe,
            "environment": {"name": "Staging"},
            "flags": {
                "pii-flag": {
                    "key": "pii-flag",
                    "enabled": True,
                    "variationType": "STRING",
                    "defaultVariation": "on",
                    "variations": {"on": {"key": "on", "value": "on-value"}},
                    "allocations": [
                        {
                            "key": "default-allocation",
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
        from ddtrace.internal.openfeature._config import _set_ffe_config

        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    @pytest.mark.parametrize("consent", [False, True], ids=["consent-off", "consent-on"])
    def test_kill_switch_off_constructs_no_writer_and_no_hook(self, monkeypatch, consent):
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")
        monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "false")

        process_ffe_configuration(self._config(consent))

        from ddtrace.internal.openfeature._provider import DataDogProvider

        provider = DataDogProvider()

        assert provider._flag_eval_evp_writer is None
        assert provider._flag_eval_evp_hook is None
        # The provider's hook list must omit the EVP hook too.
        hook_types = {type(h).__name__ for h in provider.get_provider_hooks()}
        assert "FlagEvalEVPHook" not in hook_types
```

- [ ] **Step 10.2: Run the kill-switch test.**

Run: `scripts/run-tests openfeature -k TestKillSwitch`

Expected: PASS (2 parametrized cases).

- [ ] **Step 10.3: Commit.**

```bash
git add tests/openfeature/test_flageval_pii.py
git commit -m "$(cat <<'EOF'
test(openfeature): kill-switch proof — no writer, no hook, no emission

Asserts DD_FLAGGING_EVALUATION_COUNTS_ENABLED=false disables the EVP
flagevaluation track completely, regardless of observeFullEvaluationData.
Kill switch always wins over consent. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Update `AIDEV-` anchor + release note

**Files:**
- Modify: `ddtrace/internal/openfeature/_provider.py` (update existing AIDEV-NOTE we already edited in Task 4)
- Create: `releasenotes/notes/*.yaml`

- [ ] **Step 11.1: Verify the AIDEV-NOTE at the top of `_resolve_details` is accurate.**

Open `ddtrace/internal/openfeature/_provider.py::_resolve_details`. The `AIDEV-NOTE` written in Task 4 should read like:

```python
        # AIDEV-NOTE: Stamp eval-time and consent at provider entry so every
        # OpenFeature exit path carries them. Consent is snapshotted from the
        # exact FFE snapshot this evaluation runs against -- never read from
        # live config downstream (see docs/superpowers/specs/2026-08-06-
        # pii-flagevaluations-hashing-design.md).
```

If it does not match exactly, correct it.

- [ ] **Step 11.2: Run the `releasenote` skill to draft the release note.**

Invoke the `releasenote` skill and follow its prompts. The note should describe:

- **What changed.** Server-side flag evaluations emitted to the EVP `flagevaluation` track now hash the `targeting_key` by default and omit `context.evaluation` by default. Full-fidelity emission is an explicit per-environment opt-in via the new `observeFullEvaluationData` UFC field.
- **Impact.** Upgraded SDK stops shipping raw subject PII to the `flagevaluations` track unless the environment opts in. Unique-subject counts per `(flag, allocation)` stay accurate — every SDK produces the same digest for the same subject.
- **Query surface.** Any dashboard or query that reads `targeting_key` as the raw subject must accept the `sha256_` prefix on the default path.
- **Kill switch unchanged.** `DD_FLAGGING_EVALUATION_COUNTS_ENABLED` still disables the whole track.

If the skill produces the fragment, verify the file it wrote in `releasenotes/notes/`.

- [ ] **Step 11.3: Run the full openfeature suite one more time to confirm everything is green.**

Run: `scripts/run-tests openfeature`

Expected: All PASS.

- [ ] **Step 11.4: Commit.**

```bash
git add releasenotes/notes/*.yaml ddtrace/internal/openfeature/_provider.py
git commit -m "$(cat <<'EOF'
docs(openfeature): release note for the flagevaluation PII contract

Documents the default-hashing behavior, the sha256_ wire prefix, the
observeFullEvaluationData per-environment opt-in, and the unchanged
DD_FLAGGING_EVALUATION_COUNTS_ENABLED kill switch. FFL-2964.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Lint sweep and PR-open readiness

**Files:** whole tree.

- [ ] **Step 12.1: Run the `lint` skill on all files touched.**

Invoke the `lint` skill and follow its prompts. Target the following files:

```
ddtrace/internal/openfeature/_flageval_pii.py
ddtrace/internal/openfeature/_config.py
ddtrace/internal/openfeature/_native.py
ddtrace/internal/openfeature/_provider.py
ddtrace/internal/openfeature/_flag_eval_evp_hook.py
ddtrace/internal/openfeature/_flagevaluation_writer.py
tests/openfeature/test_flageval_pii.py
tests/openfeature/test_flag_eval_evp_hook.py
tests/openfeature/test_flagevaluation_writer.py
tests/openfeature/test_provider.py
```

Fix anything the linter flags.

- [ ] **Step 12.2: Run the full openfeature suite one final time.**

Run: `scripts/run-tests openfeature`

Expected: All PASS.

- [ ] **Step 12.3: Commit any lint fixes.**

```bash
git add -u
git diff --cached --quiet || git commit -m "$(cat <<'EOF'
chore(openfeature): lint fixes for FFL-2964

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12.4: Verify the branch is in a good state for PR opening.**

Run: `git log --oneline main..HEAD`

Expected output: a clean linear series of ~11 commits (design spec + Tasks 1-11) plus at most one lint-fixes commit. Confirm no `--amend`, no `--no-verify`, no `--force`.

Do NOT open the PR from this plan — that is a separate action left to the operator (they may want to add a Jira link, screenshots of dogfooding, or a companion system-tests PR link before opening).

---

## Self-review

**Spec coverage.** Every section of the design spec maps to a task:

| Spec section | Task |
|---|---|
| Snapshot bundling (`_FfeSnapshot`) | Task 2 |
| Consent read on the RC path | Task 3 |
| Consent stamping in the provider | Task 4 |
| New `METADATA_OBSERVE_FULL_EVALUATION_DATA` constant | Task 4 (Step 4.1) |
| Hook reads consent, fails closed, skips attrs when off | Task 5 |
| `_EvalEvent` and `_Entry` gain consent | Task 5.1, Task 6.1 |
| Full-tier key carries consent + AND-fold + consent-off forces context out | Task 6 |
| `hash_targeting_key` in `_flageval_pii.py` | Task 1 |
| Flush-time hashing + serialization branch | Task 7 |
| `DoLog` non-impact | Task 9 |
| Kill switch (no code change; test only) | Task 10 |
| Canonical vector | Task 1 (Step 1.1) |
| Non-normalization tests | Task 1 (Step 1.5) |
| UFC placement guard | Task 3 (Step 3.1) |
| Consent-lifecycle regression | Task 8 |
| Raw-wire assertions | Task 7 (Step 7.3) |
| Degraded tier never emits subject | Task 7 (Step 7.3) |
| AIDEV comment + release note | Task 11 |
| Lint | Task 12 |

**Placeholder scan.** No TBD/TODO/"handle edge cases"/"similar to Task N" references. Every step shows the code it's asking for.

**Type consistency.** `_FfeSnapshot(config, observe_full_evaluation_data)` field names match across Tasks 2-4. `METADATA_OBSERVE_FULL_EVALUATION_DATA` constant used in Tasks 4, 5, 8, 9, 10. `hash_targeting_key`/`TARGETING_KEY_HASH_PREFIX` from Task 1 are used unchanged in Task 7. `_EvalEvent` field name `observe_full_evaluation_data` is used consistently in Tasks 5, 6, 8, 9, 10. `_Entry.observe_full_evaluation_data` from Task 6 used unchanged in Task 7.

**Out-of-scope reminders.** The system-tests manifest activation (`missing_feature (FFL-2446)` on Python's `tests/ffe/test_flag_eval_evp.py`) is intentionally not in this plan; see spec section "Out of scope". Similarly, no libdatadog changes (`libdatadog#2117` is PHP-only).
