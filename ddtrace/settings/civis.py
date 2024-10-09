import typing as t

from envier import En


class CIVisConfig(En):
    __prefix__ = "dd.civisibility"

    itr_enabled = En.v(
        bool,
        "itr_enabled",
        default=False,
        help_type="Boolean",
        help="Enable ....",
    )

    agentless_enabled = En.v(
        bool,
        "agentless_enabled",
        default=False,
        help_type="Boolean",
        help="Enable ....",
    )

    agentless_url = En.v(
        str,
        "agentless_url",
        default="",
        help_type="String",
        help="Enable ....",
    )

    log_level = En.v(
        str,
        "log_level",
        default="info",
        help_type="String",
        help="Enable ....",
    )

    early_flake_detection = En.v(
        bool,
        "early_flake_detection",
        default=True,
        help_type="Boolean",
        help="Enable ....",
    )


class CITestConfig(En):
    __prefix__ = "dd.test"

    session_name = En.v(
        t.Optional[str],
        "session.name",
        default=None,
        help_type="String",
        help="....",
    )


ci_config = CIVisConfig()
test_config = CITestConfig()
