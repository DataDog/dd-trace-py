from ddtrace.internal.utils.deprecations import deprecate


deprecate(
    "ddtrace.commands is deprecated",
    message="Use ddtrace_commands.ddtrace_run instead.",
    removal_version="0.5",
)
