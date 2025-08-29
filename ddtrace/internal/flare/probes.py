import abc
import dataclasses
import logging
from typing import Any
from typing import Dict
from typing import List
import sys

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclasses.dataclass
class FlareProbe(abc.ABC):
    module: str
    line: int

    @abc.abstractmethod
    def _hook(self, probe_data: Dict[str, Any]) -> None:
        pass

    def hook(self, probe_data: Dict[str, Any]) -> None:
        try:
            self._hook(probe_data)
        except Exception as e:
            log.warning("Error executing flare probe hook: %s", e)


@dataclasses.dataclass
class LogProbe(FlareProbe):
    level: int
    message: str
    locals: List[str] = dataclasses.field(default_factory=list)
    logger: logging.Logger = dataclasses.field(init=False)

    def __post_init__(self):
        logger = logging.getLogger(self.module)
        self.logger = logger

    def _hook(self, probe_data: Dict[str, Any]) -> None:
        f_locals: Dict[str, Any] = {}
        if self.locals:
            try:
                # Get caller frame
                #   - 0 is this function (_hook)
                #   - 1 is hook()
                #   - 2 is the actual caller we want
                caller = sys._getframe(2)
                f_locals = {key: caller.f_locals.get(key, caller.f_globals.get(key, None)) for key in self.locals}
            except Exception as e:
                log.warning("Error getting flare probe locals: %s", e)
                f_locals = {key: None for key in self.locals}

        message = self.message
        if f_locals:
            try:
                message = message.format(**f_locals)
            except Exception as e:
                self.logger.warning("Error formatting flare probe message: %s", e)

        self.logger.log(self.level, message)
