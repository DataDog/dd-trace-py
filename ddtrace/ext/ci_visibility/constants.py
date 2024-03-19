from enum import Enum


DEFAULT_SESSION_NAME = "ci_visibility_session"


class DEFAULT_OPERATION_NAMES(Enum):
    SESSION = "ci_visibility.session"
    MODULE = "ci_visibility.module"
    SUITE = "ci_visibility.suite"
    TEST = "ci_visibility.test"
    UNSET = "ci_visibility.unset"
