---

name: dependency-direction-analysis
description: >
  Run the dependency direction detector against ddtrace and propose architectural
  fixes for any violations found. Use this when adding or refactoring modules
  under ddtrace/internal, ddtrace/contrib, or any product package, or when the
  detect_layering_violations CI job reports new violations on a PR.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - TodoWrite
---

# Dependency Direction Analysis Skill

This skill runs the dependency direction detector locally and proposes sound
architectural fixes for any violations found. It enforces three rules:

1. **`ddtrace.internal` and `ddtrace.contrib` must not depend on product code.**
   They are shared foundation layers; every product depends on them, so a
   dependency running the other way creates hidden coupling and risks circular
   imports (see the `circular-import-analysis` skill).
2. **Products must not depend on each other directly.** Tracing, AppSec, AI
   Guard, LLM Observability, Profiling, Dynamic Instrumentation, CI
   Visibility, Error Tracking, OpenFeature, OpenTelemetry, and Runtime metrics
   are each isolated: none of them are mandatory for a given dd-trace-py
   install, so one product can't assume another is present.
3. **Products must not depend on `ddtrace.contrib` directly** (config:
   `rules.forbid_products_into_contrib` in `layers.json`, enabled by default).
   Contrib exists to attach integration-specific behaviour to a product
   (Pattern 1 below: dispatch events, let contrib listen), never the other
   way round. A product module importing `ddtrace.contrib` directly means
   integration-specific knowledge (a per-library constant, a per-library
   helper) leaked into code every install runs, regardless of which
   integrations are active.

The guiding principle is the same as circular-import analysis: **Separation of
Concerns**. Fixes must restructure ownership or add a decoupling layer, not
paper over the problem with deferred imports.

## When to Use This Skill

- The `detect_layering_violations` CI job reports new violations on your PR.
- You are adding a new module, or an import, that crosses from `ddtrace/internal`,
  `ddtrace/contrib`, or one product package into another product package.
- You are adding a brand new top-level `ddtrace/<x>` package or module and the
  CI job reports it as an uncovered/uncategorized top-level module.
- You are refactoring and want to verify you haven't introduced a new violation.

## Running the Analysis

```bash
uv run --script scripts/import-analysis/layers.py analyze violations.json
```

This writes the results to `violations.json` and prints a summary to stdout.
Requires `uv` on `PATH` (`brew install uv` or `pip install uv`). The output has
two top-level keys:

```json
{
  "violations": [ ... ],
  "uncovered": [ "ddtrace.newthing" ]
}
```

`violations` entries look like:
```json
{
  "from": "ddtrace.internal.tracemethods",
  "to": "ddtrace.trace",
  "from_zone": "internal-core",
  "to_zone": "product:tracing",
  "score": 139,
  "in_tangle": true
}
```

- `from` / `to` — the two modules the violating import connects.
- `from_zone` / `to_zone` — which side of the rule they fall on (`internal-core`,
  `contrib`, or `product:<name>`).
- `score` — how bad this specific edge is (see "Severity scoring" below).
- `in_tangle` — the imported module is also part of a strongly connected
  component larger than one module, i.e. this violation is compounding an
  existing circular-import problem, not just crossing a boundary once.

`uncovered` lists direct children of the `ddtrace` package root (packages or
`.py` modules) that are neither a key in `layers.json`'s `zones` map nor listed
in `foundation.top_level`. This is what catches a new top-level submodule that
was added without anyone deciding which zone it belongs to — without it, a new
package like `ddtrace/newproduct/` would silently be treated as exempt
foundation code and get zero dependency-direction enforcement. Unlike
violations, a new entry here always fails CI on `compare` (see below),
regardless of severity — it represents a config gap, not a graded issue.

To compare against the base branch the way CI does (new vs. pre-existing vs.
worsened vs. removed, for both violations and uncovered modules):

```bash
uv run --script scripts/import-analysis/layers.py compare violations-base.json violations-pr.json
```

Clean up afterwards:
```bash
rm violations.json violations-base.json violations-pr.json
```

## Finding hidden per-product ownership in shared zones

`layers.py` only catches internal-core/contrib code that *imports* a
product. It can't catch the opposite smell: a module that lives in one of
the zones supposed to serve every product but is, in practice, only ever
*used by* one (e.g. `ddtrace.internal.writer` is only imported by tracing
code) -- that module was never generic and belongs in that product's own
namespace instead of the shared foundation layer.

`affinity.py` looks the other direction across the same import graph: for
every module in a zone under analysis, who imports it, and does that set of
importers belong overwhelmingly to one product?

```bash
uv run --script scripts/import-analysis/affinity.py analyze affinity.json
uv run --script scripts/import-analysis/affinity.py report affinity.json
```

By default it checks every zone in `layers.json`'s
`rules.forbid_from_zones_into_products` -- the same list `layers.py` already
uses to mean "must serve every product" -- rather than hardcoding a single
zone name. Today that's `internal-core` and `contrib`. Pass `--zones
internal-core` to narrow it if you only want one.

`analyze` writes `affinity.json` with a `candidates` list, each entry giving
the module, its resolved `dominant_zone`, a `confidence` tier, the breakdown
of importer zones, a `confirmed_violations` list (see below), and (for the
weaker tier) which importers don't fit the pattern:

- **Confirmed** — `confirmed_violations` is non-empty: at least one direct
  importer of this module already trips a `layers.py` rule (most often rule 3
  above, a product importing straight into `contrib`). This isn't a
  heuristic judgement call, it's the same exact-edge detection `layers.py`
  uses for CI, just surfaced here alongside the affinity data, and it's
  reported regardless of `dominant_ratio` -- a module used broadly enough
  that no single product dominates (e.g. `ddtrace.contrib.trace_utils`,
  `ratio` well under the dominant threshold) can still contain a confirmed
  violation; breadth of use doesn't excuse one bad edge. Fix these the same
  way you'd fix a `layers.py` violation (see the patterns below); `report`
  lists these first, separately from the two heuristic tiers.
- **`exclusive`** — every importer with a resolvable zone belongs to the same
  product, and none of those imports are themselves confirmed violations.
  Strongest heuristic signal; a good candidate to move into
  `ddtrace.internal.<product>` (see Pattern 4/5 below) or reclassify in
  `layers.json`.
- **`dominant`** — one product accounts for most (≥60%) of the importers, but
  not all, and none of those imports are confirmed violations. The
  `minority_importers` field lists the outliers -- read those first: they're
  either a legitimate shared use (leave the module where it is) or evidence
  that *they* are the ones reaching into the wrong place (e.g. another
  product borrowing this one's internals informally).

`dominant_ratio` is votes for the top product divided by *all* importers with
any resolvable zone, not just the ones that voted -- an importer with no
usable signal (foundation code like `ddtrace.bootstrap.preload`, or a
same-zone importer that hasn't itself resolved to a product) still counts
against the denominator instead of being dropped. This matters: a module
imported once by a product and once by generic bootstrap/plugin code isn't
"100% exclusive" to that product just because the bootstrap caller doesn't
vote for anyone -- its presence is itself evidence the module is more
broadly used than the vote count alone would suggest.

Detection propagates through same-zone chains: if module A is only imported
by module B, and B itself resolved to product X, A inherits X's vote too.
This lets it catch indirection (a low-level helper used only by a
product-specific wrapper) that a single-hop check would miss. This makes the
`exclusive`/`dominant` tiers heuristic rather than exact -- unlike
`confirmed_violations`, they aren't wired into CI and shouldn't gate
anything; treat them as a prioritized list of places to look, not a verdict.

**Namespace buckets:** some packages are deliberately structured so each
child module belongs to a different product by design -- e.g.
`ddtrace.internal.settings` holds the generic `Config` aggregator
(`_config.py`) plus one settings module per product (`settings.profiling`,
`settings.aiguard`, ...), each contributed independently. Flagging
`settings.profiling` as "exclusive to profiling" would be true but not
actionable: it was never meant to be generic, so there's nothing to
relocate. `layers.json`'s `affinity.namespace_buckets` lists such packages;
`affinity.py` drops their direct children from the `exclusive`/`dominant`
tiers (a `confirmed_violations` hit still surfaces regardless -- the bucket
doesn't excuse an actual `layers.py` rule violation). Add a new prefix here
when you hit the same shape elsewhere, rather than treating every
single-product hit under it as a relocation candidate.

**On contrib results:** most `ddtrace.contrib` modules come back `exclusive`
by construction -- each integration patches one library for one product, so
"this contrib module is only used by product X" is rarely surprising on its
own, and that's expected (see the `contrib -> product:tracing` exception in
`layers.json`). But don't dismiss every contrib entry as noise: check
`confirmed_violations` first. A contrib module whose only importer is, say,
`ddtrace._trace.trace_handlers` isn't "product-specific by design" -- it's
evidence of rule 3 above being broken (integration-specific logic living in
code the product runs unconditionally). Run with `--zones internal-core` only
once you've looked at the confirmed section, if you want to drop the
remaining structural contrib noise.

Once you've picked a real candidate, apply Pattern 4 or 5 from "Architectural
Patterns for Fixing Violations" below, then re-run `layers.py analyze` to
confirm the move didn't introduce a `layers.json`-visible violation the other
way.

## Zone Configuration

Zones are defined in `scripts/import-analysis/layers.json`, keyed by module
prefix (longest match wins), so a product's own `ddtrace.internal.<product>`
subpackage (e.g. `ddtrace.internal.appsec`) is carved out of the
`ddtrace.internal` catch-all and treated as part of that product, not as
foundation code. Modules with no matching prefix (e.g. `ddtrace.ext`,
`ddtrace.propagation`, `ddtrace.vendor`) are unclassified "foundation" code and
are exempt from every rule, both as importer and as imported module.

`layers.json` also has an `exceptions` list of zone-pairs that are deliberately
exempt from the rules — this is how we record a considered decision without
touching detection logic. For example, `ddtrace/contrib/*` modules are tracer
integrations by design, so `contrib -> product:tracing` is listed as an
exception rather than flagged on every run.

**Only add an exception when the dependency is intentional and durable** — not
as a shortcut to make CI pass. If you're unsure whether an edge should be an
exception or a bug, ask; this is a business/architecture decision, not
something to infer from the code.

### Fixing a new "uncovered top-level module" finding

When the CI job (or `analyze`) reports a new entry under `uncovered`, someone
added a new direct child of `ddtrace/` (a package or a `.py` module) that
`layers.json` doesn't know about yet. Resolve it by editing
`scripts/import-analysis/layers.json`:

- If it's a new product (mandatory-or-not feature area, isolated from other
  products), add it to `zones` as `"ddtrace.<name>": "product:<name>"`, and
  add its `ddtrace.internal.<name>` counterpart too if one exists.
- If it's shared foundation code that everything may depend on and that
  itself has no restrictions (like `ddtrace.ext` or `ddtrace.propagation`),
  add it to `foundation.top_level`.
- If it's a carve-out of an existing product (e.g. a new
  `ddtrace.internal.<product>` subpackage), map it to that product's zone
  rather than leaving it to fall through to `internal-core`.

Don't add it to `foundation.top_level` just to silence the check — that
defeats the point of the coverage check. Ask if it's unclear which zone fits.

## Severity Scoring

Each violation's `score` combines three structural signals (no git history
involved):

- **Rule weight** — `internal-core`/`contrib` violations start higher (3) than
  product-vs-product violations (1), because foundation code reaching upward
  is a worse inversion than two peers leaking into each other.
- **Afferent coupling of the target** (`ca` from betsy's `ModuleMetrics`) — how
  many other modules already depend on the module being imported. A violation
  that reaches into a heavily-relied-upon module has a bigger blast radius to
  eventually unwind.
- **Cycle bonus (+5)** — added when the imported module's `nccd` (from betsy)
  is greater than 1.0, i.e. it's already part of an import tangle. Fixing the
  layering violation first often makes the tangle easier to break too.

Use the score to prioritize: fix the highest-scoring violations first,
especially any marked `in_tangle`.

## Architectural Patterns for Fixing Violations

> **Never use deferred imports (`import x` inside a function body) as a fix.**
> They hide the structural problem and impose a runtime cost on every call.

### Understand the edge first

```bash
# What exactly does <from> import from <to>?
grep -n "^import ddtrace\|^from ddtrace" <path/to/from/module>.py
```

Identify the exact names crossing the boundary before choosing a fix — often
only a small fraction of the target module is actually needed.

---

### Pattern 1 — Core event bus (for `contrib` -> product violations)

**When to use:** A contrib integration wants to notify or be observed by a
product (this is the most common shape for `contrib -> product:X`
violations). This is the documented pattern in
`.cursor/rules/isolated-responsibility.mdc`.

The contrib patch dispatches an event; it does not import the product:

```python
from ddtrace.internal import core

core.dispatch(f"{event}.before", (kwargs,), allow_raise=True)
resp = func(*args, **kwargs)
core.dispatch(f"{event}.after", (kwargs, resp), allow_raise=True)
```

The product registers a listener, guarded by its own enable flag, inside its
own package — not inside `contrib`:

```python
from ddtrace.internal import core

def load_my_product():
    core.on("some.integration.before", _before_handler)
```

Neither side imports the other; `ddtrace.internal.core` is foundation code
both may depend on.

---

### Pattern 2 — Dependency inversion (for `internal-core` -> product violations)

**When to use:** `ddtrace.internal` needs to call into a product, but the
product also needs to be the one driving behavior (e.g. registering a hook,
supplying a callback).

Define a `Protocol` or abstract base inside `ddtrace.internal` (or a small
neutral module); the product implements it and registers itself explicitly.
`ddtrace.internal` depends on the abstraction, never on the concrete product
package.

---

### Pattern 3 — Extract shared types into a third, unclassified module

**When to use:** Two zones share a data type, constant, or protocol that both
legitimately need, but neither should own.

Create a thin module outside both zones' prefixes (so it's unclassified
foundation code, e.g. `ddtrace._types` or similar) containing only the shared
contract. Both sides import from it; neither imports from the other.

---

### Pattern 4 — Move the code to the zone that owns it

**When to use:** The violation exists because a function/class ended up in
the wrong package. This is the simplest and often best fix.

If `ddtrace.internal.tracemethods` calls something that conceptually belongs
to the tracing product, move it into `ddtrace.trace`/`ddtrace._trace` so the
dependency direction reverses: the product depends on internal-core (allowed),
not the other way round.

---

### Pattern 5 — Question whether the target should be foundation code

**When to use:** A product-to-product violation involves a genuinely
general-purpose utility that happens to live inside a product package (e.g.
a formatting helper under `ddtrace.trace` that other products also want).

Move the utility down into `ddtrace.internal` (or an unclassified module) so
every product can depend on it without depending on each other. Don't do this
for anything that's conceptually part of the product's public contract (e.g.
`Tracer`, `Span`) — those stay put, and the dependency on them should go
through Pattern 1 or 2 instead.

---

## Decision checklist before proposing a fix

1. **Identify the exact cross-boundary names** — grep the violating file.
2. **Classify the relationship:**
   - Contrib notifying/observing a product → Pattern 1 (core event bus)
   - internal-core needs product behavior → Pattern 2 (dependency inversion)
   - Shared data type/constant → Pattern 3 (extract)
   - Wrong home for the code → Pattern 4 (move)
   - Misplaced general-purpose utility → Pattern 5 (relocate to foundation)
3. **Consider whether this is actually an intentional, durable dependency** —
   if so, propose adding it to `layers.json`'s `exceptions` list instead of
   restructuring code, but say so explicitly and explain why; this is a call
   for the humans reviewing the PR, not something to decide unilaterally.
4. **Verify** by re-running `uv run --script scripts/import-analysis/layers.py analyze violations.json`
   after the change and confirming the violation is gone (or, if compared
   against a saved base snapshot, that it doesn't appear as new).
