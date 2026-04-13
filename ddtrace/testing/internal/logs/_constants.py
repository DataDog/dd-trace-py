# Datadog logs intake per-entry constraints.
# Both LogsHandler and StderrCapture enforce these so they live here as the
# single source of truth, imported by the submodules without touching __init__.

MAX_MESSAGE_BYTES = 1 * 1024 * 1024  # 1 MB
TRUNCATION_SUFFIX = "... [truncated]"
