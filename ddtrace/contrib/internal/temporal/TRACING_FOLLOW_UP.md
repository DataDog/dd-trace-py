# Temporal tracing follow-up decisions

The Events/Subscribers migration deliberately preserves the integration's current public behavior. It changes where
tracing decisions are implemented, but it does not decide whether those decisions are the product behavior we want to
support long term.

## Tracing-policy changes left for follow-up

The current subscriber reproduces the existing operation names, parent relationships, span kinds, tags, error handling,
and sampling behavior. A separate review should decide whether to change any of these policies:

- Entry-point operations currently receive a manual keep decision when they have no in-process parent. This includes
  client workflow operations, schedules, and standalone activities. We should confirm that this sampling override is
  necessary and consistent with other orchestration integrations.
- `RunWorkflow` remains open for the lifetime of a workflow. This accurately represents execution duration and preserves
  a coherent trace across worker restarts, but it can retain child spans until partial flush occurs. We should decide
  whether the long-running span model, a task-oriented model, or a hybrid should be the supported product model.
- Workflow, signal, query, update, activity, and Nexus operations retain their current span names, kinds, resources, and
  tag schema. These should be reviewed against Datadog semantic conventions before they become a stable contract.
- Deterministic span and trace IDs remain compatible with the current implementation and the Go SDK algorithm. Any change
  needs an explicit cross-language compatibility decision and a migration plan for in-flight workflows.
- Query spans and update-validator spans are still emitted during the same phases as before. Whether these operations
  should be traced, measured, or sampled differently is a product decision.

These items were excluded because combining policy changes with the ownership refactor would make trace-shape regressions
harder to distinguish from intentional behavior changes.

## Additional public configuration left for follow-up

The interceptor constructor remains the only interface for some Temporal-specific options. A follow-up can decide
whether to expose any of the following through `config.temporal`:

- the propagation header key;
- extra span tags;
- invalid-parent handling;
- workflow trace-lifetime or partial-flush guidance.

The `on_span_finish` callback remains a manual-interceptor feature. It should not be converted into environment-based
configuration because it accepts application code.

Before adding settings, define their names, defaults, precedence relative to constructor arguments, telemetry reporting,
and remote-configuration behavior. Public settings should be documented and tested as compatibility commitments.

## Recommended next review

Start with the span model and sampling rules. Capture representative traces for long-running workflows, ContinueAsNew,
worker restart, signals, updates, child workflows, standalone activities, and Nexus operations. Use those examples to
agree on the intended trace shape, then add configuration only for choices users need to control operationally.
