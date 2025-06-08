from collections import namedtuple
import typing


DDFrame = namedtuple("DDFrame", ["file_name", "lineno", "function_name", "class_name"])
StackTraceType = typing.List[DDFrame]
