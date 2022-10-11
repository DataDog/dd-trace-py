import os
import typing as t
from warnings import warn

from envier import En


def parse_venv(value):
    # type: (str) -> t.Optional[str]
    try:
        return os.path.abspath(value) if value is not None else None
    except TypeError:
        warn(
            "No virtual environment detected. Running without a virtual environment active might "
            "cause exploration tests to instrument more than intended."
        )


class ExplorationConfig(En):
    venv = En.v(
        t.Optional[str],
        "virtual_env",
        parser=parse_venv,
        default=None,
    )

    encode = En.v(
        bool,
        "dd.debugger.expl.encode",
        default=True,
        help="Whether to encode the snapshots",
    )

    status_messages = En.v(
        bool,
        "dd.debugger.expl.status_messages",
        default=False,
        help="Whether to print exploration debugger status messages",
    )

    include = En.v(
        list,
        "dd.debugger.expl.include",
        parser=lambda v: [path.split(".") for path in v.split(",")],
        default=[],
        help="List of module paths to include in the exploration",
    )

    elusive = En.v(
        bool,
        "dd.debugger.expl.elusive",
        default=False,
        help="Whether to include elusive modules in the exploration",
    )

    class ProfilerConfig(En):
        __item__ = "profiler"
        __prefix__ = "dd.debugger.expl.profiler"

        enabled = En.v(
            bool,
            "enabled",
            default=True,
            help="Whether to enable the exploration deterministic profiler",
        )

    class CoverageConfig(En):
        __item__ = "coverage"
        __prefix__ = "dd.debugger.expl.coverage"

        enabled = En.v(
            bool,
            "enabled",
            default=True,
            help="Whether to enable the exploration line coverage",
        )

        delete_probes = En.v(
            bool,
            "delete_line_probes",
            default=False,
            help="Whether to delete line probes after they are triggered",
        )


config = ExplorationConfig()
