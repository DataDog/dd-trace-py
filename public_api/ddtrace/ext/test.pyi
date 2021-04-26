from enum import Enum

ARGUMENTS: str
TEST_ARGUMENTS: str
FRAMEWORK: str
TEST_FRAMEWORK: str
NAME: str
TEST_NAME: str
SKIP_REASON: str
TEST_SKIP_REASON: str
STATUS: str
TEST_STATUS: str
SUITE: str
TEST_SUITE: str
TRAITS: str
TEST_TRAITS: str
TYPE: str
TEST_TYPE: str

class Status(Enum):
    PASS: str = ...
    FAIL: str = ...
    SKIP: str = ...
