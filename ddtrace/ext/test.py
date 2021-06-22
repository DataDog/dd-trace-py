"""
tags for common test attributes
"""

from enum import Enum


# Test Framework
FRAMEWORK = "test.framework"

# Test Name
NAME = "test.name"

# Test Parameters
PARAMETERS = "test.parameters"

# Pytest Result (XFail, XPass)
RESULT = "pytest.result"

# Skip Reason
SKIP_REASON = "test.skip_reason"

# Test Status
STATUS = "test.status"

# Test Suite
SUITE = "test.suite"

# Traits
TRAITS = "test.traits"

# Test Type
TYPE = "test.type"

# XFail Reason
XFAIL_REASON = "pytest.xfail.reason"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    XFAIL = "xfail"
    XPASS = "xpass"
