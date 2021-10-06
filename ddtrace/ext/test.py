"""
tags for common test attributes
"""

from enum import Enum


# Test Arguments
ARGUMENTS = TEST_ARGUMENTS = "test.arguments"

# Test Framework
FRAMEWORK = TEST_FRAMEWORK = "test.framework"

# Test Framework Version
FRAMEWORK_VERSION = TEST_FRAMEWORK_VERSION = "test.framework_version"

# Test Name
NAME = TEST_NAME = "test.name"

# Test Parameters
PARAMETERS = "test.parameters"

# Pytest Result (XFail, XPass)
RESULT = TEST_RESULT = "pytest.result"

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

# XFail Reason
XFAIL_REASON = TEST_XFAIL_REASON = "pytest.xfail.reason"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    XFAIL = "xfail"
    XPASS = "xpass"
