# This file is part of "echion" which is released under MIT.
#
# Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

import os
import typing as t


def attach(config: t.Dict[str, str], pipe_name: t.Optional[str] = None) -> None:
    os.environ["ECHION_CPU"] = str(int(config["cpu"]))
    os.environ["ECHION_NATIVE"] = str(int(config["native"]))
    os.environ["ECHION_OUTPUT"] = config["output"]
    os.environ["ECHION_STEALTH"] = str(int(config["stealth"]))
    os.environ["ECHION_WHERE"] = str(int(bool(config["where"])))

    from echion.bootstrap import start

    # This is used in where mode for IPC.
    if pipe_name is not None:
        from echion.core import set_pipe_name

        set_pipe_name(pipe_name)

    start()


def detach() -> None:
    from echion.bootstrap import stop

    stop()
