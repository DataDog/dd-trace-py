"""
tags for common test attributes
"""

from enum import Enum

# Test Arguments
TEST_ARGUMENTS = "test.arguments"

# Test Framework
TEST_FRAMEWORK = "test.framework"

# Test Name
TEST_NAME = "test.name"

# Skip Reason
TEST_SKIP_REASON = "test.skip_reason"

# Test Status
TEST_STATUS = "test.status"
STATUS = TEST_STATUS

# Test Suite
TEST_SUITE = "test.suite"

# Traits
TEST_TRAITS = "test.traits"

# Test Type
TEST_TYPE = "test.type"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
