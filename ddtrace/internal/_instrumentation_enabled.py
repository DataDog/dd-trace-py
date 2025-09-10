import os


_INSTRUMENTATION_ENABLED: bool = os.getenv("_DD_INSTRUMENTATION_DISABLED", "false").lower() in {"false", "1"}
