from io import TextIOWrapper
from pathlib import Path
import sys
import typing as t
from warnings import warn

from envier import En


def parse_venv(value: str) -> t.Optional[Path]:
    try:
        return Path(value).resolve() if value is not None else None
    except TypeError:
        warn(
            "No virtual environment detected. Running without a virtual environment active might "
            "cause exploration tests to instrument more than intended."
        )


class InjectionConfig(En):
    venv = En.v(
        t.Optional[Path],
        "virtual_env",
        parser=parse_venv,
        default=None,
    )

    status_messages = En.v(
        bool,
        "dd.bytecode.injection.status_messages",
        default=False,
        help="Whether to print bytecode injecter status messages",
    )

    include = En.v(
        list,
        "dd.bytecode.injection.include",
        parser=lambda v: [path.split(".") for path in v.split(",")],
        default=[],
        help="List of module paths to include in the injection",
    )

    elusive = En.v(
        bool,
        "dd.bytecode.injection.elusive",
        default=False,
        help="Whether to include elusive modules in the injection",
    )

    output_file = En.v(
        t.Optional[Path],
        "dd.bytecode.injection.output_file",
        default=None,
        help="Path to the output file. The standard output is used otherwise",
    )

    output_stream = En.d(
        TextIOWrapper,
        lambda c: c.output_file.open("a") if c.output_file is not None else sys.__stdout__,
    )


config = InjectionConfig()
