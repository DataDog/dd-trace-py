import typing as t

from envier import En


class CIVisConfig(En):
    __prefix__ = "dd.civisibility"

    _itr_enabled = En.v(
        bool,
        "itr.enabled",
        default=False,
        help_type="Boolean",
        help="Enable ....",
    )

    _agentless_enabled = En.v(
        bool,
        "agentless.enabled",
        default=False,
        help_type="Boolean",
        help="Enable ....",
    )

    _agentless_url = En.v(
        str,
        "agentless.url",
        default="",
        help_type="String",
        help="Enable ....",
    )

    log_level = En.v(
        str,
        "log.level",
        default="info",
        help_type="String",
        help="Enable ....",
    )

    early_flake_detection = En.v(
        bool,
        "early.flake.detection",
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
