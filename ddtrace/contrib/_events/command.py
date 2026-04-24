from dataclasses import dataclass
from enum import Enum
from typing import Union

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


class CommandEvents(Enum):
    OS_SYSTEM = "command.os_system"
    SUBPROCESS_POPEN = "command.subprocess_popen"


@dataclass
class ShellCommandEvent(Event):
    event_name = CommandEvents.OS_SYSTEM.value

    command: str = event_field()


@dataclass
class ProcessCommandEvent(Event):
    event_name = CommandEvents.SUBPROCESS_POPEN.value

    command_args: Union[list[str], str] = event_field()
