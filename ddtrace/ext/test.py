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

# Test Command
COMMAND = "test.command"

# Test Module
MODULE = "test.module"

# Test Module Path
MODULE_PATH = "test.module_path"

# Test Suite
SUITE = TEST_SUITE = "test.suite"

# Test Name
NAME = TEST_NAME = "test.name"

# Test Parameters
PARAMETERS = "test.parameters"

# Pytest Result (XFail, XPass)
RESULT = TEST_RESULT = "pytest.result"

# Skip Reason
SKIP_REASON = TEST_SKIP_REASON = "test.skip_reason"

# Test skipped by ITR
SKIPPED_BY_ITR = "skipped_by_itr"

# Test Status
STATUS = TEST_STATUS = "test.status"

# Traits
TRAITS = TEST_TRAITS = "test.traits"

# Test Type
TYPE = TEST_TYPE = "test.type"

# Test File
# Use when test implementation file is different from test suite name.
FILE = TEST_FILE = "test.file"

# Test Class Hierarchy
CLASS_HIERARCHY = "test.class_hierarchy"

# Test Codeowners
CODEOWNERS = TEST_CODEOWNERS = "test.codeowners"

ITR_TEST_SKIPPING_TYPE = "test.itr.tests_skipping.type"

ITR_TEST_SKIPPING_COUNT = "test.itr.tests_skipping.count"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    XFAIL = "xfail"
    XPASS = "xpass"
