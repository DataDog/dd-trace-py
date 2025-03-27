import enum
import logging
import time
import typing


MINUTE = 60.0
HOUR = 60.0 * MINUTE
DAY = 24.0 * HOUR


class LogOption:
    def __init__(self, name: str, level: int, duration: float = 0.0) -> None:
        self._level = level
        self._level_name = logging.getLevelName(level).lower()
        self._name = f"{name}::{self._level_name}"
        self._duration = duration


class TelemetryOption(LogOption, enum.Enum):
    METRICS = ("telemetry_metrics", logging.WARNING, HOUR)
    LOGS = ("telemetry_logs", logging.WARNING, HOUR)


class AppsecOption(LogOption, enum.Enum):
    ASM_CONTEXT = ("asm_context", logging.WARNING, DAY)
    ASM_CONTEXT_DEBUG = ("asm_context", logging.DEBUG)


def get_time(message: LogOption, info: str, _cache: typing.Dict[tuple[str, str], float] = {}) -> bool:
    if message._duration == 0.0:
        return True
    key = message._name, info
    if key not in _cache:
        _cache[key] = time.monotonic()
        return True
    previous = _cache[key]
    now = time.monotonic()
    if now - previous > message._duration:
        _cache[key] = now
        return True
    return False


class AppsecLogger:
    def __init__(self, filename: str, product: str):
        self._logger = logging.getLogger(filename)
        self._product = product

    def log(self, message: LogOption, info: str, more_info: str = "", context: bool = False, exc_info: bool = False):
        if get_time(message, info):
            if context:
                filename, line_number, function_name, _stack_info = self._logger.findCaller(False, 4)
                string = (
                    f"{self._product}::{message._name}::{info}{more_info}"
                    f" [{filename}, line {line_number}, in {function_name}]"
                )
            else:
                string = f"{self._product}::{message._name}::{info}{more_info}"
            self._logger.log(message._level, string, exc_info=exc_info)
