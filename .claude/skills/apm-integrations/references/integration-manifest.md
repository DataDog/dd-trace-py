# Integration Manifest

Every integration directory `ddtrace/contrib/internal/{name}/` ships a
`{name}.manifest.yaml`: declarative, machine-readable metadata describing what
the integration instruments. Consumers such as the Integration Portal read it
instead of parsing tracer code. It has no runtime effect and is excluded from
the wheel and sdist (`MANIFEST.in`).

Not to be confused with the LLMObs **agent manifest**
(`ddtrace/llmobs/_integrations/agent_manifest.py`), which describes an AI
agent at runtime. The two are unrelated.

## When to touch it

| Change | Manifest action |
|--------|-----------------|
| New integration | Add `{name}.manifest.yaml` in the same PR |
| Integration removed | Delete the manifest with the directory |
| Activating PyPI distribution added/removed (`dependency_names` in `registry.yaml` changes) | Update `packages` |
| Integration starts instrumenting a new kind of operation (e.g. a client-only integration gains server spans, a cloud SDK gains LLM calls) | Update `category`, and `description` if it no longer covers the operation |
| Integration gains dedicated handling for a new statically known backend (e.g. botocore adds an AWS service) | Update `systems` |
| Integration renamed | Update `name` / `display_name` |
| Bug fix, refactor, new tags, config options, supported-version bump, migration to a new patching API | **No change** — this is the common case |

Never change `creation_date` on an existing manifest. When updating, change
only the affected fields; leave the others alone unless the source shows they
are wrong.

## Output shape

Every field is required. Keep this order and add no other fields:

```yaml
_v: 1
name: aiokafka
display_name: aiokafka
description: Instruments aiokafka message publishing and consumption with distributed tracing and Data Streams Monitoring,
  including consumer offset tracking.
packages:
- name: aiokafka
category:
- messaging.consumer
- messaging.producer
systems:
- kafka
creation_date: '2025-11-18'
```

`category` and `systems` are sets: no duplicates, serialized in lexical order.
`category` must be non-empty; `systems` may be empty. Read 1-2 manifests from
integrations of the same type before writing one (e.g. `flask`, `aiokafka`,
`psycopg`, `httplib`, `langgraph`).

## Workflow

Derive every value from the integration's source, the registry, and Git
history. Do not copy values from a similar-looking integration without
checking that the source supports them.

### 1. Fix the name

`name` is the registry `integration_name` in
`scripts/integration_registry/registry.yaml`, which also equals the directory
name and the `config._add()` key. It matches `^[a-z0-9_]+$`. Do not substitute
a distribution name, import name, or helper module.

### 2. Inventory the patch surface

Read:

1. The registry row, especially `dependency_names` and `is_external_package`.
2. `patch.py`, plus the helpers, wrappers, event handlers, and resource tables
   under `ddtrace/contrib/internal/{name}/`.
3. The integration docstring in `__init__.py`.
4. Shared helpers reached from the patched entry points (e.g. `dbapi`,
   `trace_utils`, WSGI/ASGI middleware).

Record what Datadog deliberately wraps or observes, and the customer-facing
operation behind each boundary. Separate real capabilities from the transport,
serialization, propagation, and tagging that implement them. Behavior the
library offers but the integration does not instrument does not belong in the
manifest.

Upstream library source or docs may clarify what an already-wrapped symbol
does or how the library spells its own name. Never use them to add a
description claim, category, or system that the patch code does not support.

### 3. Identity and display metadata

- `_v` — always `1`.
- `packages` — copy registry `dependency_names`, one `- name:` entry each,
  sorted. External integrations need at least one package; stdlib or
  ddtrace-internal integrations (`httplib`, `logging`, `asgi`, `wsgi`) use `[]`.
- `display_name` — the library's established spelling and capitalization as it
  appears in its docs or the integration docstring (`Flask`, `HTTPX`,
  `aiokafka`, `LangGraph`). Do not title-case `name` mechanically.
- `description` — one sentence, normally starting "Instruments ...", usually
  15-25 words. Describe each distinct instrumented boundary once at the
  operation-family level (e.g. "message publishing and consumption", "sync,
  async, and streaming completions"). Do not list individual wrapped methods
  unless needed to disambiguate; method inventories go stale. Do not restate
  the category and system fields, use marketing prose, or claim behavior that
  is only available upstream.

### 4. Category

Publish every category whose definition the instrumented behavior meets, and
only those:

- Each category must trace back to a boundary in the inventory. Wrapping a
  function or emitting a span proves observation, not a separate category.
- Keep multiple categories for distinct capabilities (e.g. `http.server` and
  `template.render` for Flask). There is no primary category and no limit.
- For a single operation, prefer its specific semantic family over the
  underlying protocol (an LLM API call over HTTP is `gen_ai`, not
  `http.client`).
- Directional categories are independent: client vs. server and producer vs.
  consumer must each be proven. A database or cache command wrapper does not
  become messaging because it may see `PUBLISH` or `SUBSCRIBE`.
- There is one GenAI category, `gen_ai`, regardless of operation type.
- `other` is exclusive: use `[other]` only after a full review finds nothing
  else that fits.

The category vocabulary is a shared taxonomy that does not live in this
repository. Reuse values from existing manifests
(`ddtrace/contrib/internal/*/*.manifest.yaml`). If no existing value fits,
flag it in the PR description rather than inventing one.

### 5. Systems

List the concrete backends, providers, services, or platforms the integration
is statically bound to (`postgresql`, `kafka`, `redis`, `aws.s3`, `openai`):

- Prove both the identity and its binding to an instrumented operation through
  a dedicated client, constrained dispatch, or an explicitly instrumented
  provider path.
- A hardcoded tag, span type, or protocol helper alone is not enough. Check
  whether the same hook accepts other, runtime-selected backends; if it does,
  the backend is not a system.
- Do not turn arbitrary protocols, formats, frameworks, or package names into
  systems.
- Use `systems: []` only when the inventory shows no concrete binding, as with
  generic HTTP clients, web frameworks, and agent frameworks that call any
  model provider.

As with categories, reuse values from existing manifests and flag anything new
in the PR description.

### 6. Creation date

`creation_date` is the quoted `'YYYY-MM-DD'` author date of the first commit
that introduced working instrumentation:

```bash
git --no-pager log --follow --diff-filter=A --date=short --format='%H %ad %s' -- ddtrace/contrib/internal/{name}/patch.py
```

- Follow legacy paths (`ddtrace/contrib/{name}/`, renamed modules) back to the
  original implementation.
- A commit that only adds autopatching, registry wiring, automatic enablement,
  or moves/refactors files is not the creation commit. Inspect suspicious
  commits and keep going back.
- Do not use file modification time, the package's release date, or today's
  date. For a new integration, use the author date of the branch commit that
  adds the patch code.

### 7. Validate

- [ ] File is `ddtrace/contrib/internal/{name}/{name}.manifest.yaml`
- [ ] All fields present, in order, with no extra keys; `display_name` and `description` non-empty
- [ ] `name` equals the directory name and registry `integration_name`
- [ ] `packages` matches registry `dependency_names`
- [ ] `category` non-empty, unique, sorted; `other` used alone or not at all
- [ ] `systems` unique, sorted; an empty list is backed by the inventory
- [ ] Every category and system traces back to the patch code, not upstream docs
- [ ] `creation_date` comes from a commit that added instrumentation; unchanged on existing manifests
