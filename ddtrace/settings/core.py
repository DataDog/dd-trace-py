import typing as t

from envier import En


class CoreConfig(En):
    __prefix__ = "dd"

    env = En.v(
        t.Optional[str],
        "env",
        default=None,
        help_type="String",
        help="Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. "
        "See `Unified Service Tagging`_ for more information.",
    )


config = CoreConfig()
