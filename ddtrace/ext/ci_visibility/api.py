"""
Provides the API necessary to interacting with the CI Visibility service.

All functions in this module are meant to be called in a stateless manner. Test runners (or custom implementations) that
rely on this API are not expected to keep CI Visibility-related state for each session, module, suite or test.

Stable values of module, suite, test names, and parameters, are a necessity for this API to function properly.

All types and methods for interacting with the API are provided and documented in this file.
"""
import dataclasses
from typing import Dict

from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase, _CIVisibilityItemIdBase
from ddtrace.ext.ci_visibility.util import _catch_and_log_exceptions

@dataclasses.dataclass(frozen=True)
class CISessionId(_CIVisibilityItemIdBase):
    session_id: str

@dataclasses.dataclass(frozen=True)
class CIModuleId(_CIVisibilityItemIdBase):
    module_name: str
@dataclasses.dataclass(frozen=True)
class CISuiteId(_CIVisibilityItemIdBase):
    module_name: str
    suite_name: str

@dataclasses.dataclass(frozen=True)
class CITestId(_CIVisibilityItemIdBase):
    module_name: str
    suite_name: str
    test_name: str
    test_parameters: Dict[str, any]
    retry_number: int = 0

class CISession(_CIVisibilityAPIBase):

    @staticmethod
    @_catch_and_log_exceptions
    def register():
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def start(use_discovery: bool = False, use_intelligent_test_runner: bool = True, reject_unknown_items: bool= False):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def finish(force_finish_session_modules: bool = False):
        pass
    @staticmethod
    @_catch_and_log_exceptions
    def get_skippable_items():
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_settings():
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_known_tests():
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_tag(tag_name: str, tag_value: str):
        pass
    @staticmethod
    @_catch_and_log_exceptions
    def get_tags(tags: Dict[str, str]):
        pass
    @staticmethod
    @_catch_and_log_exceptions
    def set_tag(tag_name: str, tag_value: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def set_tags(tags: Dict[str, str]):
        pass

    @classmethod
    @_catch_and_log_exceptions
    def delete_tag(cls, tag_name: str):
        pass

    @classmethod
    @_catch_and_log_exceptions
    def delete_tags(cls, tag_name: str):
        pass



class CITestModule(_CIVisibilityAPIBase):
    @staticmethod
    @_catch_and_log_exceptions
    def register(module_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get(module_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def start(module_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def set_tag(tag_name: str, tag_value: str):
        pass
    @staticmethod
    @_catch_and_log_exceptions
    def set_tags(module_name: str, tags: Dict[str, str]):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def delete_tag(module_name: str, tag_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def finish(module_name: str, force_finish_suites: bool = False):
        pass

class CITestSuite(_CIVisibilityAPIBase):
    @staticmethod
    @_catch_and_log_exceptions
    def register(item_id: CISuiteId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get(item_id: CISuiteId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CISuiteId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def finish(item_id: CISuiteId, force_finish_children: bool = False):
        pass
    @staticmethod
    @_catch_and_log_exceptions
    def set_tag(item_id: CISuiteId, tag_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_tag(item_id: CISuiteId, tag_name: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, any]):
        pass


class CITest(_CIVisibilityAPIBase):

    @staticmethod
    @_catch_and_log_exceptions
    def register(item_id: CITestId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get(item_id: CITestId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CITestId):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def finish(item_id: CITestId, status: str):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, any]):
        pass
