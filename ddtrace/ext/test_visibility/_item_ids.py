"""Provides identifier classes for items used in the Test Visibility API

NOTE: BETA - this API is currently in development and is subject to change.
"""
import dataclasses
from typing import Optional

from ddtrace.ext.test_visibility._test_visibility_base import _TestVisibilityChildItemIdBase
from ddtrace.ext.test_visibility._test_visibility_base import _TestVisibilityRootItemIdBase


@dataclasses.dataclass(frozen=True)
class TestModuleId(_TestVisibilityRootItemIdBase):
    name: str

    def __repr__(self):
        return "TestModuleId(module={})".format(
            self.name,
        )


@dataclasses.dataclass(frozen=True)
class TestSuiteId(_TestVisibilityChildItemIdBase[TestModuleId]):
    def __repr__(self):
        return "TestSuiteId(module={}, suite={})".format(self.parent_id.name, self.name)


@dataclasses.dataclass(frozen=True)
class TestId(_TestVisibilityChildItemIdBase[TestSuiteId]):
    parameters: Optional[str] = None  # For hashability, a JSON string of a dictionary of parameters

    def __repr__(self):
        return "TestId(module={}, suite={}, test={}, parameters={})".format(
            self.parent_id.parent_id.name,
            self.parent_id.name,
            self.name,
            self.parameters,
        )
