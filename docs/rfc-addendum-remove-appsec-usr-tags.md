# [RFC Addendum] Removal of `_dd.appsec.usr.id` and `_dd.appsec.usr.login` tags

**Amends:** [RFC] Automated user lifecycle tracking (Accepted, Oct 21, 2024)
**Author:** Florentin Labelle
**Status:** Proposed
**Date:** 2025

## Context & Motivation

The original RFC introduced two internal, write-only span tags to support
*accuracy analysis* of automated user ID/login collection:

- `_dd.appsec.usr.id` — a duplicate of `usr.id` emitted only on
  auto-collected (non-SDK) requests, so the backend could compare the
  auto-instrumented value against the SDK-provided one.
- `_dd.appsec.usr.login` — a duplicate of the public
`appsec.events.users.{login,signup}.*.usr.login` event tags, emitted only
on auto-collected events, for the same correlation purpose.

An audit of the Python tracer (and cross-check against the RFC's own
examples) shows that these tags are **redundant in every collection mode**:

| Scenario | `_dd.appsec.usr.id` vs `usr.id` | `_dd.appsec.usr.login` vs public event tag |
|----------|--------------------------------|--------------------------------------------|
| Pure-auto, identification | identical value, identical value | identical value |
| Pure-auto, anonymization | identical (both hashed) | identical (both hashed) |
| SDK + auto, identification, auto accurate | identical | identical |
| SDK + auto, anonymization | **differ** (raw vs hashed) | **differ** (raw vs hashed) |
| SDK + auto, identification, auto inaccurate | **differ** | **differ** |

The only cases where the internal tags carry information not already
available elsewhere are the last two rows — both require the **SDK to be
in use simultaneously with auto-instrumentation**, and the divergence is
fully derivable from the existing `_dd.appsec.user.collection_mode` /
`_dd.appsec.events.*.auto.mode` tags together with `usr.id` and the
public `*.usr.login` event tags.

For the (majority) case of customers using **only** auto-instrumentation
(no SDK), the internal tags are a verbatim duplicate of `usr.id` and the
public event tags in both identification and anonymization modes, and
carry zero additional information.

No tracer code reads these tags; they are emitted exclusively for
backend consumption.

## Proposed Change

Remove the `_dd.appsec.usr.id` and `_dd.appsec.usr.login` span tags from
the specification and from all library implementations. Specifically:

1. **Authenticated user tracking** — remove the `_dd.appsec.usr.id`
   requirement. The `usr.id` and `_dd.appsec.user.collection_mode` tags
   remain sufficient to identify that the user ID was collected
   automatically and in which mode.

2. **Login success / failure / signup events** — remove both
   `_dd.appsec.usr.id` and `_dd.appsec.usr.login`. The public event tags
   (`appsec.events.users.{login,signup}.*.usr.{login,id}`) and `usr.id`
   remain the authoritative carriers of user identity for business logic
   events.

3. **Backend accuracy analysis** — the backend must derive the
   auto-vs-SDK comparison from `_dd.appsec.user.collection_mode`
   (value `sdk` vs `identification`/`anonymization`) and the
   `_dd.appsec.events.*.sdk` tag, comparing `usr.id` against the public
   event tags where both are present. No dedicated duplicate tag is
   required.

## Scope of removal (per library)

Each library implementation may remove:

- The constant definitions for the two tags.
- All `set_attribute` write sites.
- The `if mode != SDK` conditionals that existed solely to gate emission
  of these tags (the surrounding mode logic, hashing, `set_user`, and
  public event tags are unaffected).
- Any dead locals computed only for these tags (e.g. a hashed login
  computed solely to populate `_dd.appsec.usr.login`).
- Test assertions on the removed tags.

No public API, customer-facing tag, libddwaf address, telemetry metric,
or configuration setting is affected.

## Backward compatibility

- **No customer-facing tag is removed.** `usr.id`, `usr.login`, and the
  `appsec.events.*` event tags are unchanged.
- **Backend ingestion** must stop relying on `_dd.appsec.usr.id` /
  `_dd.appsec.usr.login` as the source of auto-collected user identity.
  Because the values were always duplicates of `usr.id` / the public event
  tags (except in the SDK+auto divergence cases, which are derivable from
  `collection_mode`), no data loss occurs if the backend reads the
  remaining tags instead.
- Libraries may ship the removal in a minor release; no major-version
  bump is required since the removed tags are internal (`_dd.`-prefixed)
  and were never part of the documented public contract.

## Rollout

1. Backend confirms it no longer consumes `_dd.appsec.usr.id` /
   `_dd.appsec.usr.login` (or derives the same signal from
   `collection_mode` + `usr.id` + public event tags).
2. Libraries remove the tags and update tests.
3. This addendum is merged, retiring the tags from the RFC specification.

## Appendix: Examples after removal

The RFC's Appendix B examples are updated by deleting the two internal
tag lines from each example. For instance, authenticated user tracking
in identification mode becomes:

```json
{
  "_dd.appsec.user.collection_mode": "identification",
  "usr.id": "1023712892"
}
```

And an automated login success event in identification mode with user ID
becomes:

```json
{
  "appsec.events.users.login.success.track": "true",
  "_dd.appsec.events.users.login.success.auto.mode": "identification",
  "appsec.events.users.login.success.usr.login": "zouzou@sansgluten.com",
  "usr.id": "1023712892",
  "manual.keep": "true"
}
```

All other tags in every example are unchanged.
