import abc
from enum import Enum
import functools
import json
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generic
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import TypeVar
from typing import Union

from ddtrace import Span
from ddtrace import Tracer
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import DEFAULT_OPERATION_NAMES
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.ext.ci_visibility.api import CITestStatus
from ddtrace.internal.ci_visibility.api.ci_coverage_data import CICoverageData
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilitySessionSettings(NamedTuple):
    tracer: Tracer
    test_service: str
    test_command: str
    test_framework: str
    test_framework_version: str
    session_operation_name: str
    module_operation_name: str
    suite_operation_name: str
    test_operation_name: str
    root_dir: Path
    reject_unknown_items: bool = True
    reject_duplicates: bool = True


class SPECIAL_STATUS(Enum):
    UNFINISHED = 1


CIDT = TypeVar("CIDT", CIModuleId, CISuiteId, CITestId)  # Child item ID types
ITEMT = TypeVar("ITEMT", bound="CIVisibilityItemBase")  # All item types
PIDT = TypeVar("PIDT", CISessionId, CIModuleId, CISuiteId)  # Parent item ID types
ANYIDT = TypeVar("ANYIDT", CISessionId, CIModuleId, CISuiteId, CITestId)  # Any item ID type


def _require_not_finished(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.is_finished():
            log.warning("Method %s called on item %s, but it is already finished", func, self.item_id)
            return
        return func(self, *args, **kwargs)

    return wrapper


class CIVisibilityItemBase(abc.ABC, Generic[ANYIDT]):
    event_type = "unset_event_type"

    def __init__(
        self,
        item_id: ANYIDT,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, Any]],
        parent: Optional["CIVisibilityItemBase"] = None,
    ):
        self.item_id: ANYIDT = item_id
        self.parent: Optional["CIVisibilityItemBase"] = parent
        self.name = self.item_id.name
        self._status: CITestStatus = CITestStatus.FAIL
        self._session_settings = session_settings
        self._tracer = session_settings.tracer
        self._service = session_settings.test_service
        self._operation_name = DEFAULT_OPERATION_NAMES.UNSET.value

        self._span: Optional[Span] = None
        self._tags = initial_tags if initial_tags else {}
        self._children = None

        self._is_itr_skipped: bool = False

        # Internal state keeping
        self._status_set = False

        # General purpose-attributes not used by all item types
        self._codeowners: Optional[List[str]] = []
        self._source_file_info: Optional[CISourceFileInfo] = None
        self._coverage_data: Optional[CICoverageData] = None

    def __repr__(self):
        return f"{self.__class__.__name__}({self.item_id})"

    def _add_all_tags_to_span(self):
        for tag, tag_value in self._tags.items():
            try:
                if isinstance(tag_value, str):
                    self._span.set_tag_str(tag, tag_value)
                else:
                    self._span.set_tag(tag, tag_value)
            except Exception as e:
                log.debug("Error setting tag %s: %s", tag, e)

    def _start_span(self):
        parent_span = self.get_parent_span()

        self._span = self._tracer._start_span(
            self._operation_name,
            child_of=parent_span,
            service=self._service,
            span_type=SpanTypes.TEST,
        )

    def _finish_span(self):
        self._set_default_tags()
        self._set_test_hierarchy_tags()
        self._add_coverage_data()

        # Allow item-level _set_span_tags() to potentially overwrite default and hierarchy tags.
        self._set_span_tags()

        self._add_all_tags_to_span()
        self._span.finish()

    def _set_default_tags(self):
        """Applies the tags that should be on every span regardless of the item type

        All spans start with test.STATUS set to FAIL, in order to ensure that no span is accidentally
        reported as successful.
        """

        self.set_tags(
            {
                EVENT_TYPE: self.event_type,
                SPAN_KIND: "test",
                test.FRAMEWORK: self._session_settings.test_framework,
                test.FRAMEWORK_VERSION: self._session_settings.test_framework_version,
                test.COMMAND: self._session_settings.test_command,
                test.STATUS: self._status.value,  # Convert to a string at the last moment
            }
        )

        if self._codeowners:
            self.set_tag(test.CODEOWNERS, json.dumps(self._codeowners))

        if self._source_file_info is not None:
            if self._source_file_info.path:
                self.set_tag(test.SOURCE_FILE, self._source_file_info.path)
            if self._source_file_info.start_line is not None:
                self.set_tag(test.SOURCE_START, self._source_file_info.start_line)
            if self._source_file_info.end_line is not None:
                self.set_tag(test.SOURCE_END, self._source_file_info.end_line)

        if self._is_itr_skipped:
            self.set_tag(test.SKIP_REASON, SKIPPED_BY_ITR_REASON)
            self.set_tag(test.ITR_SKIPPED, "true")

    def _set_span_tags(self):
        """This is effectively a callback method for exceptional cases where the item span
        needs to be modified directly by the class

        Only use if absolutely necessary.

        Classes that need to specifically modify the span directly should override this method.
        """
        pass

    @abc.abstractmethod
    def _get_hierarchy_tags(self):
        raise NotImplementedError("This method must be implemented by the subclass")

    def _collect_hierarchy_tags(self) -> Dict[str, str]:
        """Collects all tags from the item's hierarchy and returns them as a single dict"""
        tags = self._get_hierarchy_tags()
        parent = self.parent
        while parent is not None:
            tags.update(parent._get_hierarchy_tags())
            parent = parent.parent
        return tags

    def _set_test_hierarchy_tags(self):
        """Add module, suite, and test name and id tags"""
        self.set_tags(self._collect_hierarchy_tags())

    def start(self):
        self._start_span()

    def finish(self, force: bool = False):
        """Finish the span and set the _is_finished flag to True.

        Nothing should be called after this method is called.
        """
        self._finish_span()

    def is_finished(self):
        return self._span is not None and self._span.finished

    def get_span_id(self):
        if self._span is None:
            return None
        return self._span.span_id

    def get_status(self) -> Union[CITestStatus, SPECIAL_STATUS]:
        if self.is_finished():
            return self._status
        return SPECIAL_STATUS.UNFINISHED

    def get_raw_status(self) -> CITestStatus:
        return self._status

    def set_status(self, status: CITestStatus):
        if self.is_finished():
            error_msg = f"Status already set for item {self.item_id}"
            log.warning(error_msg)
            return
        self._status_set = True
        self._status = status

    def mark_itr_skipped(self):
        self._is_itr_skipped = True

    @_require_not_finished
    def set_tag(self, tag_name: str, tag_value: Any) -> None:
        self._tags[tag_name] = tag_value

    @_require_not_finished
    def set_tags(self, tags: Dict[str, Any]) -> None:
        for tag in tags:
            self._tags[tag] = tags[tag]

    @_require_not_finished
    def get_tag(self, tag_name: str) -> Any:
        return self._tags[tag_name]

    @_require_not_finished
    def get_tags(self, tag_names: List[str]) -> Dict[str, Any]:
        tags = {}
        for tag_name in tag_names:
            tags[tag_name] = self._tags[tag_name]

        return tags

    @_require_not_finished
    def delete_tag(self, tag_name: str) -> None:
        del self._tags[tag_name]

    # @_require_not_finished
    def delete_tags(self, tag_names: List[str]) -> None:
        for tag_name in tag_names:
            del self._tags[tag_name]

    def get_span(self):
        return self._span

    def get_parent_span(self):
        if self.parent is not None:
            self.parent.get_span()

    @abc.abstractmethod
    def add_coverage_data(self, coverage_data: Dict[Path, List[Tuple[int, int]]]):
        pass

    def _add_coverage_data(self):
        if self._coverage_data:
            self._span.set_tag_str(
                COVERAGE_TAG_NAME, self._coverage_data.build_payload(self._session_settings.root_dir)
            )


class CIVisibilityChildItem(CIVisibilityItemBase, Generic[CIDT]):
    item_id: CIDT


CITEMT = TypeVar("CITEMT", bound="CIVisibilityChildItem")


class CIVisibilityParentItem(CIVisibilityItemBase, Generic[PIDT, CIDT, CITEMT]):
    def __init__(
        self,
        item_id: PIDT,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, Any]],
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self.children: Dict[CIDT, CITEMT] = {}

    def _are_all_children_finished(self):
        return all(child._is_finished() for child in self.children.values())

    def get_status(self) -> Union[CITestStatus, SPECIAL_STATUS]:
        """Recursively computes status based on all children's status

        - FAIL: if any children have a status of FAIL
        - SKIP: if all children have a status of SKIP
        - PASS: if all children have a status of PASS
        - UNFINISHED: if any children are not finished

        The caller of get_status() must decide what to do if the result is UNFINISHED
        """
        if self.children is None:
            return self.get_status()

        # We use values because enum entries do not hash stably
        children_status_counts = {
            CITestStatus.FAIL.value: 0,
            CITestStatus.SKIP.value: 0,
            CITestStatus.PASS.value: 0,
        }

        for child in self.children.values():
            child_status = child.get_status()
            if child_status == SPECIAL_STATUS.UNFINISHED:
                # There's no point in continuing to count if we care about unfinished children
                log.debug("Item %s has unfinished children", self.item_id)
                return SPECIAL_STATUS.UNFINISHED
            children_status_counts[child_status.value] += 1

        log.debug("Children status counts for %s: %s", self.item_id, children_status_counts)

        if children_status_counts[CITestStatus.FAIL.value] > 0:
            return CITestStatus.FAIL
        if children_status_counts[CITestStatus.SKIP.value] == len(self.children):
            return CITestStatus.SKIP
        # We can assume the current item passes if not all children are skipped, and there were no failures
        if children_status_counts[CITestStatus.FAIL.value] == 0:
            return CITestStatus.PASS

        # If we somehow got here, something odd happened and we set the status as FAIL out of caution
        return CITestStatus.FAIL

    def finish(self, force: bool = False, override_status: Optional[CITestStatus] = None):
        """Recursively finish all children and then finish self

        An unfinished status is not considered an error condition (eg: some order-randomization plugins may cause
        non-linear ordering of children items).

        force results in all children being finished regardless of their status

        override_status only applies to the current item. Any unfinished children that are forced to finish will be
        finished with whatever status they had at finish time (in reality, this should mean that any unfinished
        children are marked as failed, since that is the default status set upon start)
        """
        if override_status:
            # Respect override status no matter what
            self.set_status(override_status)

        item_status = self.get_status()

        if item_status == SPECIAL_STATUS.UNFINISHED:
            if force:
                # Finish all children regardless of their status
                for child in self.children.values():
                    if not child.is_finished():
                        child.finish(force=force)
                self.set_status(self.get_raw_status())
                return

            else:
                # Leave the item as unfinished if any children are unfinished
                return
        elif not isinstance(item_status, SPECIAL_STATUS):
            self.set_status(item_status)

        super().finish()

    def add_child(self, child: CITEMT):
        child.parent = self
        if self._session_settings.reject_duplicates and child.item_id in self.children:
            error_msg = f"{child.item_id} already exists in {self.item_id}'s children"
            log.warning(error_msg)
            raise CIVisibilityDataError(error_msg)
        self.children[child.item_id] = child

    def get_child_by_id(self, child_id: CIDT) -> CITEMT:
        if child_id in self.children:
            return self.children[child_id]
        error_msg = f"{child_id} not found in {self.item_id}'s children"
        raise CIVisibilityDataError(error_msg)
