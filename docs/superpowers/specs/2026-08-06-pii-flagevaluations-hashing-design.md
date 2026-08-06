# FFL-2964: Protect PII in the `flagevaluation` EVP track (dd-trace-py)

## Status

Design accepted 2026-08-06. Ready for implementation planning.

## Source contract

- RFC: `rfc:gdoc:19VIf4B9p-zsAL2uWSvdzil59DZlGjQm4yudAMwJBqLs` (mirrored at `ffe-codegen-tools/plugins/ffe-codegen-tools/references/sources/rfcs/ffe/2026-07-15-protecting-pii-flagevaluations-evp.md`).
- Pilot: `pr:github:DataDog/dd-trace-java#12042`.
- Reference implementation in a peer server SDK: `pr:github:DataDog/dd-trace-go#5151` (open, on branch `vickie/ffl-2962-protecting-pii-in-flagevaluations-track`). Structure and test layout mirror it.
- Cluster README with the stable cross-SDK contract: `ffe-codegen-tools/plugins/ffe-codegen-tools/references/sources/prs/pii-flagevaluations-hashing/README.md`.
- Jira: FFL-2964 (this SDK's ticket), umbrella FFL-2780, server SDK fan-out FFL-2784.

## Cross-SDK contract

Every SDK must reproduce all of these behaviors byte-for-byte.

- Top-level UFC boolean `observeFullEvaluationData`. Sibling of `environment`, **not** nested under it. Absent, explicit `null`, and wrong-typed values all fail closed to `false`.
- Consent-off (default): `targeting_key` is emitted as `sha256_` + 64-char lowercase hex (71 chars total). `context.evaluation` is omitted entirely — absent key, not `null`, not `{}`.
- Consent-on: `targeting_key` is emitted raw, verbatim. `context.evaluation` is emitted raw.
- Hash is unsalted SHA-256 over the raw UTF-8 bytes of the targeting key. No trimming, case folding, or Unicode normalization.
- Canonical cross-SDK vector, asserted in every SDK's unit tests: `"jane.doe@datadoghq.com"` → `sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b`.
- Hashing applies regardless of evaluation outcome, including `FLAG_NOT_FOUND` and runtime-default evaluations.
- Consent travels on OpenFeature `FlagResolutionDetails.flag_metadata` under the unprefixed snake_case key `observe_full_evaluation_data`. Each SDK's evaluator stamps it from the exact UFC snapshot it evaluated against; the hook reads it from `flag_metadata`, never from live configuration.
- The kill switch `DD_FLAGGING_EVALUATION_COUNTS_ENABLED` still wins over everything and emits nothing.
- `DoLog` must not gate this behavior. Each SDK owes a proof that the emitted shape is byte-identical across `do_log ∈ {True, False}` for each consent value.

## Scope

**In scope for FFL-2964 (this PR):**

- Read `observeFullEvaluationData` from the top level of the UFC dict in `ddtrace/internal/openfeature/_native.py::process_ffe_configuration`.
- Bundle the parsed UFC and its consent value in a single atomic snapshot in `_config.py`.
- Stamp `observe_full_evaluation_data` onto `flag_metadata` inside `DataDogProvider._resolve_details` on every return path, from the snapshot the evaluation ran against.
- Have `FlagEvalEVPHook` read consent from `details.flag_metadata`, fail closed on missing/malformed values, and skip attribute capture when consent is off.
- Extend `_EvalEvent`, `_Entry`, and the full-tier bucket key tuple to carry consent. AND-fold consent into the entry on the fast path.
- Add `ddtrace/internal/openfeature/_flageval_pii.py` with `hash_targeting_key` and the `sha256_` prefix constant. Hashing runs at flush cadence inside `FlagEvaluationWriter.periodic()`, matching Go/Java.
- Add unit and integration tests covering the RFC's validation requirements.

**Out of scope, tracked elsewhere:**

- system-tests manifest activation. The Python manifest still reads `tests/ffe/test_flag_eval_evp.py: missing_feature (FFL-2446)`. Deferred; a companion PR will flip the file to active and mark only the three `Test_FFE_EVP_Flagevaluation_ObserveFullData_*` rows as `missing_feature (FFL-2964)`.
- libdatadog changes. dd-trace-py evaluates via the `datadog-ffe` crate (pinned at `libdatadog@v38.0.0`) for evaluation only. Hashing is in-language. `libdatadog#2117` is a PHP-only concern; dd-trace-py does not depend on it.
- UFC field parsing in the Rust native module. The RC callback already delivers `payload.content` as a Python `dict`, so we read `observeFullEvaluationData` off the dict before it is handed to native. No Rust changes required.

## Architecture

### Snapshot bundling

`ddtrace/internal/openfeature/_config.py` today exposes a single module-level global `FFE_CONFIG: Optional[ffe.Configuration]` with `_get_ffe_config` and `_set_ffe_config`. This is replaced with an atomic snapshot type paired with its consent value:

```python
class _FfeSnapshot(typing.NamedTuple):
    config: ffe.Configuration
    observe_full_evaluation_data: bool
```

`_get_ffe_config` and `_set_ffe_config` keep their public signatures (the wider codebase calls `_get_ffe_config()` in several places), but a new `_get_ffe_snapshot() -> Optional[_FfeSnapshot]` is added and used by the provider. `_set_ffe_config` accepts either a bare native config (existing tests) or a snapshot; internal storage is always the snapshot.

**Rationale.** The Java pilot's `concern:bind-consent-to-evaluated-config` review lesson: reading consent from a mutable global after evaluation lets a later Remote Config update retroactively apply another environment's consent policy. Bundling the two values in one atomic assignment closes the race at the source. Two side-by-side globals do not solve this even under a lock; only a single atomic swap does.

### Consent read on the RC path

`ddtrace/internal/openfeature/_native.py::process_ffe_configuration(config: dict)`:

1. Extract `observeFullEvaluationData` off the top-level dict. Any non-`True` value — including missing key, `False`, `None`, string, int — resolves to `False`. Explicitly:

   ```python
   consent = config.get("observeFullEvaluationData")
   observe_full_evaluation_data = consent is True
   ```

2. Continue with the existing native `Configuration(config_bytes)` construction.
3. Store the two together with `_set_ffe_config(_FfeSnapshot(native_config, observe_full_evaluation_data))`.

Nested placement under `environment` is deliberately not read — matches the RFC and the merged `dd-source#22826` UFC struct.

### Consent stamping in the provider

`DataDogProvider._resolve_details` in `_provider.py`:

1. At entry, load the snapshot: `snapshot = _get_ffe_snapshot()`.
2. Compute `observe_full_evaluation_data: bool = snapshot.observe_full_evaluation_data if snapshot else False`.
3. Add the consent to `flag_metadata` at the same point where `EVAL_TIMESTAMP_METADATA_KEY` is initialized, so every return path — success, DEFAULT, DISABLED, FLAG_NOT_FOUND, PROVIDER_NOT_READY, the outer `except` — carries it.

```python
flag_metadata: dict[str, typing.Any] = {
    EVAL_TIMESTAMP_METADATA_KEY: int(time.time() * 1000),
    METADATA_OBSERVE_FULL_EVALUATION_DATA: observe_full_evaluation_data,
}
```

4. Pass `snapshot.config` to `resolve_flag(...)` in place of the current `_get_ffe_config()` result. The evaluation uses the same config snapshot the consent value was drawn from.

**New constant.** `METADATA_OBSERVE_FULL_EVALUATION_DATA = "observe_full_evaluation_data"` in `_flagevaluation_writer.py` (alongside `EVAL_TIMESTAMP_METADATA_KEY` and `METADATA_ALLOCATION_KEY`), so hook and provider import it from the same place. Unprefixed, snake_case — the cross-SDK contract, confirmed for every SDK on 2026-07-30.

### Hook reads consent

`FlagEvalEVPHook.finally_after` in `_flag_eval_evp_hook.py`:

- Extract `observe_full_evaluation_data` from `details.flag_metadata`:

  ```python
  consent_raw = metadata.get(METADATA_OBSERVE_FULL_EVALUATION_DATA)
  observe_full_evaluation_data = consent_raw is True
  ```

  Anything not exactly `True` resolves to `False`. Matches Go's `TestExtractEvalDetailsConsentFailsClosed`.

- When consent is off, do not copy `eval_ctx.attributes` into `_EvalEvent.attrs`; pass `attrs={}` instead. Prevents the queue from retaining PII attribute dicts and matches the Go PR's optimization ("consent-off drops context at serialization and from the bucket key, so keeping the caller's attributes alive in the queue serves nothing").

- Attach `observe_full_evaluation_data` to the enqueued `_EvalEvent`.

### `_EvalEvent` and `_Entry` gain consent

`_EvalEvent` (NamedTuple in `_flagevaluation_writer.py`) grows one field:

```python
class _EvalEvent(typing.NamedTuple):
    flag_key: str
    variant: str
    allocation_key: str
    targeting_key: str
    attrs: dict[str, typing.Any]
    runtime_default: bool
    error_message: str
    eval_time_ms: int
    observe_full_evaluation_data: bool  # NEW
```

`_Entry` grows one `__slots__` entry and one constructor parameter:

```python
__slots__ = (
    "count", "first_evaluation", "last_evaluation", "runtime_default",
    "targeting_key", "context_attrs", "error_message",
    "observe_full_evaluation_data",  # NEW
)
```

### Aggregation: consent is a full-tier key dimension, AND-fold on merge

`FlagEvaluationWriter._aggregate`:

- Full-tier bucket key tuple gains `observe_full_evaluation_data` as its last element. Mixed-consent evaluations land in distinct buckets and never merge.

  ```python
  full_key = (
      event.flag_key,
      event.variant,
      event.allocation_key,
      event.runtime_default,
      event.error_message,
      event.targeting_key,
      ctx_key,
      event.observe_full_evaluation_data,  # NEW
  )
  ```

- **Before** building `ctx_key`, if `event.observe_full_evaluation_data is False`, force `context_attrs = {}` and `ctx_key = ""`. Belt-and-suspenders even though the hook also skips capture — the aggregator owns the invariant "the key carries only dimensions that survive serialization." Prevents the `concern:consent-off-bucket-keying` bug: a high-cardinality attribute like `request_id` on the privacy-protected path would otherwise inflate per-flag bucket count and force privacy-protected traffic into the degraded tier.

- On fast-path merge (bucket already exists), AND-fold consent into the entry:

  ```python
  entry = self._full[full_key]
  entry.observe_full_evaluation_data = (
      entry.observe_full_evaluation_data and event.observe_full_evaluation_data
  )
  entry.observe(event.eval_time_ms)
  ```

  Defense in depth: if any future change lets consent drift out of the key, a single consent-off observation still forces the whole bucket onto the privacy-protected wire path.

- Degraded tier: consent is **not** a degraded key dimension. Degraded already drops `targeting_key` and `context`, so consent-differing degraded rows would be byte-identical on the wire. Matches Go's `evaluationDegradedKey` comment.

### Hashing and wire serialization

New module `ddtrace/internal/openfeature/_flageval_pii.py`:

```python
"""Cross-SDK PII fingerprint for the flagevaluation EVP track."""

import hashlib

TARGETING_KEY_HASH_PREFIX = "sha256_"


def hash_targeting_key(raw: str) -> str:
    """Produce the cross-SDK fingerprint.

    Unsalted SHA-256 over the raw UTF-8 bytes — no trimming, case folding, or
    normalization — so every SDK produces a byte-identical digest and hashed
    values join across languages.

    Returns "" for empty input. Hashing "" would invent a shared pseudo-subject
    and corrupt unique-subject counts; an absent targeting_key is schema-valid.
    """
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return TARGETING_KEY_HASH_PREFIX + digest
```

Called at **flush cadence** inside `FlagEvaluationWriter.periodic()` when building full-tier events. Enqueue-time hashing was considered and rejected — bucket lookup would have to work in both hashed and raw forms, complicating the key structure. Flush-time hashing runs once per bucket per flush cycle, mirrors dd-trace-go PR #5151 and dd-trace-java PR #12042, and matches the cluster README's expected shape.

Full-tier event serialization branches on `entry.observe_full_evaluation_data`:

- `True`: `ev["targeting_key"] = entry.targeting_key` (raw); if `entry.context_attrs`, `ev["context"] = {"evaluation": entry.context_attrs}`.
- `False`: `ev["targeting_key"] = hash_targeting_key(entry.targeting_key)` (yielding `sha256_…` or `""`); **do not** set `ev["context"]` under any circumstances (absent key, not null, not `{}`).

The empty-hash case (`hash_targeting_key("") == ""`) is handled by the existing `if entry.targeting_key:` guard in `periodic()`, which already omits the key when the value is falsy. Result: consent-off evaluations with no targeting key emit no `targeting_key` field, exactly like the degraded tier.

### `DoLog` non-impact

No new `DoLog` gating anywhere in the PII path. The existing `if details.do_log:` block in `_provider.py::_resolve_details` continues to control **exposure event emission** only, which is a different EVP track (`api/v2/ffe`, not `api/v2/flagevaluation`).

### Kill switch: no change

`DD_FLAGGING_EVALUATION_COUNTS_ENABLED` is already registered in `OpenFeatureConfig.flagging_evaluation_counts_enabled` and already gates writer/hook construction in `DataDogProvider.__init__`. No change needed. Coverage is confirmed by a new test that asserts nothing is constructed and nothing is emitted regardless of `observeFullEvaluationData` when the kill switch is off.

## Files touched

| File | Change |
|------|--------|
| `ddtrace/internal/openfeature/_config.py` | Add `_FfeSnapshot`, `_get_ffe_snapshot`. Keep `_get_ffe_config`/`_set_ffe_config` backward-compatible. |
| `ddtrace/internal/openfeature/_native.py` | In `process_ffe_configuration`, read `observeFullEvaluationData` off the dict and store the snapshot. |
| `ddtrace/internal/openfeature/_provider.py` | Load snapshot in `_resolve_details`; stamp `observe_full_evaluation_data` in `flag_metadata` on every path; pass snapshot's config to `resolve_flag`. |
| `ddtrace/internal/openfeature/_flag_eval_evp_hook.py` | Read consent from metadata (fail closed); skip attrs when consent off; attach consent to `_EvalEvent`. |
| `ddtrace/internal/openfeature/_flagevaluation_writer.py` | Add `METADATA_OBSERVE_FULL_EVALUATION_DATA`. Extend `_EvalEvent`, `_Entry`. Add consent to full-tier key. Force `context_attrs={}` and `ctx_key=""` on consent-off. AND-fold on fast-path merge. Branch on consent in `periodic()` for serialization: hashed key + omit context, or raw + include context. |
| `ddtrace/internal/openfeature/_flageval_pii.py` | New. `TARGETING_KEY_HASH_PREFIX` and `hash_targeting_key`. |
| `tests/openfeature/test_flageval_pii.py` | New. Comprehensive tests mirroring dd-trace-go's `flageval_pii_test.go`. |
| `tests/openfeature/test_flagevaluation_writer.py` | Existing tests updated where they build `_EvalEvent` directly to include `observe_full_evaluation_data=True`. |
| `tests/openfeature/test_flag_eval_evp_hook.py` | Add fail-closed cases for the new metadata key. |
| `tests/openfeature/test_provider.py` | Add "stamps consent on every path" cases. |
| `releasenotes/notes/` | New fragment via the `releasenote` skill. |

## Validation Requirements (from RFC section)

Every requirement below maps to at least one test in `tests/openfeature/test_flageval_pii.py`. Where the Go PR has a test we mirror it by name; where Python idioms differ, the assertion is renamed but the intent is preserved.

- **Negative control.** With consent off, emitted `flagevaluations` events must **not** contain a raw/unhashed `targeting_key` and must **not** contain `context.evaluation`. Asserted on **raw wire bytes** (`json.dumps` output of the payload), not on decoded event objects — a decode-then-inspect check misses raw values routed into unexpected fields.
- **Green proof (default).** With consent off, the event carries `targeting_key` as `sha256_` + 64-char lowercase hex, and `context.evaluation` is absent (key missing, not `null`, not `{}`).
- **Green proof (opt-in).** With consent on, the event carries the unhashed verbatim `targeting_key` and the full `context.evaluation`.
- **Cross-SDK hash equality.** Canonical vector `"jane.doe@datadoghq.com" → sha256_b4698f9b6d186781fa8dc59e533578fa2d8379a46b1cf6db85cda6aa9c99e51b` asserted directly on `hash_targeting_key`. Same string is asserted in dd-trace-java's `ULeb128EncoderTest` and dd-trace-go's `TestHashTargetingKeyCanonicalVector`.
- **Kill-switch proof.** With `DD_FLAGGING_EVALUATION_COUNTS_ENABLED=false`, no writer and no hook are constructed and no `flagevaluations` payload is emitted regardless of `observeFullEvaluationData`.
- **`DoLog` non-impact proof.** For each consent value, flushed-event JSON is byte-identical across `do_log ∈ {True, False}` with pinned eval-time and flush-time timestamps.
- **Consent-lifecycle proof.** Two directions of the Java-pilot L3 bug that unit tests missed:
  - Evaluate under consent-off; between hook and flush, RC replaces the config with consent-on. Flushed event must still hash.
  - Evaluate under consent-on; RC replaces with consent-off before flush. Flushed event must still emit raw.
- **UFC placement guard.** `observeFullEvaluationData` nested under `environment` in the RC dict must **not** be read; consent stays False. Regression guard for the FFL-2784 ticket drift.

## Open questions

None blocking. Two things to note during implementation:

- `_set_ffe_config` currently accepts `Optional[ffe.Configuration]`. Consumers in tests and `test_prompts.py` call `_set_ffe_config(None)` for teardown. The new signature accepts `Optional[_FfeSnapshot | ffe.Configuration]`, with the bare-config form treated as consent-off. This keeps existing tests unchanged.
- Writer's existing `if entry.targeting_key:` guard in `periodic()` already omits an empty `targeting_key`. The new consent-off branch relies on that same guard to omit hashed-but-empty values, so `hash_targeting_key("")` returning `""` is correct and load-bearing.
