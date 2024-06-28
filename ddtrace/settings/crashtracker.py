import typing as t

from envier import En

from ddtrace.internal.datadog.profiling.crashtracker import crashtracker


def _derive_stacktrace_resolver(config):
    # type: (CrashtrackerConfig) -> t.Optional[str]
    resolver = config._stacktrace_resolver or ""
    resolver = resolver.lower()
    if resolver in ("fast", "full"):
        return resolver
    return None


def _check_for_crashtracker_available():
    return crashtracker.is_available


def _derive_crashtracker_enabled(config):
    # type: (CrashtrackerConfig) -> bool
    if not _check_for_crashtracker_available():
        return False
    return config._enabled


class CrashtrackerConfig(En):
    __prefix__ = "dd.crashtracker"

    _enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enables the crashtracker",
    )

    enabled = En.d(bool, _derive_crashtracker_enabled)

    debug_url = En.v(
        t.Optional[str],
        "debug_url",
        default=None,
        help_type="String",
        help="Overrides the URL parameter set by the ddtrace library.  This is for testing and debugging purposes"
        " and is not generally useful for end-users.",
    )

    stdout_filename = En.v(
        t.Optional[str],
        "stdout_filename",
        default=None,
        help_type="String",
        help="The destination filename for crashtracker stdout",
    )

    stderr_filename = En.v(
        t.Optional[str],
        "stderr_filename",
        default=None,
        help_type="String",
        help="The destination filename for crashtracker stderr",
    )

    alt_stack = En.v(
        bool,
        "alt_stack",
        default=False,
        help_type="Boolean",
        help="Whether to use an alternate stack for the crashtracker.  This is used for internal development.",
    )

    _stacktrace_resolver = En.v(
        t.Optional[str],
        "stacktrace_resolver",
        default=None,
        help_type="String",
        help="How to collect native stack traces during a crash, if at all.  Accepted values are 'none', 'fast',"
        " and 'full'.  The default value is 'none' (no stack traces).",
    )
    stacktrace_resolver = En.d(t.Optional[str], _derive_stacktrace_resolver)
