from dataclasses import dataclass
from enum import Enum
from typing import Union

from ddtrace.internal.core.events import Event


SubprocessCommand = Union[str, bytes, list[Union[str, bytes]], tuple[Union[str, bytes], ...]]


class SubprocessEvents(Enum):
    COMMAND = "subprocess.command"


@dataclass
class SubprocessCommandEvent(Event):
    event_name = SubprocessEvents.COMMAND.value

    command: SubprocessCommand
    shell: bool
