from envier import En
from envier import validators


class DatabaseMonitoringConfig(En):
    __prefix__ = "dd_trace"

    injection_mode = En.v(
        str,
        "sql_comment_injection_mode",
        default="disabled",
        help="Valid Injection Modes: disabled, service, and full",
        validator=validators.choice(["disabled", "full", "service"]),
    )


config = DatabaseMonitoringConfig()
