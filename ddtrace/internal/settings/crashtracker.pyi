from typing import Optional

from ddtrace.internal.settings._core import DDConfig

class CrashtrackingConfig(DDConfig):
    enabled: bool
    debug_url: Optional[str]
    _test_token: Optional[str]
    stdout_filename: Optional[str]
    stderr_filename: Optional[str]
    use_alt_stack: bool
    create_alt_stack: bool
    stacktrace_resolver: Optional[str]
    tags: dict[str, str]
    wait_for_receiver: bool
    collect_all_threads: bool
    max_threads: int

config: CrashtrackingConfig
