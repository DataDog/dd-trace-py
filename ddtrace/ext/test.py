"""
tags for common test attributes
"""

from enum import Enum


# Test Arguments
ARGUMENTS = TEST_ARGUMENTS = "test.arguments"

# Test Framework
FRAMEWORK = TEST_FRAMEWORK = "test.framework"

# Test Name
NAME = TEST_NAME = "test.name"

# Skip Reason
SKIP_REASON = TEST_SKIP_REASON = "test.skip_reason"

# Test Status
STATUS = TEST_STATUS = "test.status"

# Test Suite
SUITE = TEST_SUITE = "test.suite"

# Traits
TRAITS = TEST_TRAITS = "test.traits"

# Test Type
TYPE = TEST_TYPE = "test.type"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
