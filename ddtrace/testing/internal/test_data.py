from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import time
import typing as t

from ddtrace.testing.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.testing.internal.constants import TAG_TRUE
from ddtrace.testing.internal.utils import TestContext
from ddtrace.testing.internal.utils import _gen_item_id


@dataclass(frozen=True)
class ModuleRef:
    name: str


@dataclass(frozen=True)
class SuiteRef:
    module: ModuleRef
    name: str


@dataclass(frozen=True)
class TestRef:
    suite: SuiteRef
    name: str
    __test__ = False


class TestStatus(Enum):
    PASS = "pass"  # nosec: B105
    FAIL = "fail"
    SKIP = "skip"
    __test__ = False


class ITRSkippingLevel(Enum):
    SUITE = "suite"
    TEST = "test"


class TestType:
    TEST = "test"
    BENCHMARK = "benchmark"


TParentClass = t.TypeVar("TParentClass", bound="TestItem[t.Any, t.Any]")
TChildClass = t.TypeVar("TChildClass", bound="TestItem[t.Any, t.Any]")


class TestItem(t.Generic[TParentClass, TChildClass]):
    __test__ = False
    ChildClass: t.Type[TChildClass]

    def __init__(self, name: str, parent: TParentClass):
        self.name = name
        self.children: t.Dict[str, TChildClass] = {}
        self.start_ns: t.Optional[int] = None
        self.duration_ns: t.Optional[int] = None
        self.parent: TParentClass = parent
        self.item_id = _gen_item_id()
        self.status: t.Optional[TestStatus] = None
        self.tags: t.Dict[str, str] = {}
        self.metrics: t.Dict[str, t.Union[int, float]] = {}
        self.service: str = DEFAULT_SERVICE_NAME

    def seconds_so_far(self) -> float:
        if self.start_ns is None:
            raise ValueError("seconds_so_far() called before start")
        duration_ns = self.duration_ns if self.duration_ns is not None else (time.time_ns() - self.start_ns)
        return duration_ns / 1e9

    def start(self, start_ns: t.Optional[int] = None) -> None:
        self.start_ns = start_ns if start_ns is not None else time.time_ns()

    def ensure_started(self) -> None:
        if self.start_ns is None:
            self.start()

    def finish(self) -> None:
        if self.start_ns is None:
            raise ValueError("finish() called before start")

        self.set_final_tags()
        self.duration_ns = time.time_ns() - self.start_ns

    def is_started(self) -> bool:
        return self.start_ns is not None

    def is_finished(self) -> bool:
        return self.duration_ns is not None

    def get_status(self) -> TestStatus:
        if self.status is None:
            self.status = self._get_status_from_children()
        return self.status

    def set_status(self, status: TestStatus) -> None:
        self.status = status

    def set_service(self, service: str) -> None:
        self.service = service

    def _get_status_from_children(self) -> TestStatus:
        status_counts: t.Dict[TestStatus, int] = defaultdict(lambda: 0)
        total_count = 0

        for child in self.children.values():
            status = child.get_status()
            if status:
                status_counts[status] += 1
                total_count += 1

        if status_counts[TestStatus.FAIL] > 0:
            return TestStatus.FAIL

        if status_counts[TestStatus.SKIP] == total_count:
            return TestStatus.SKIP

        return TestStatus.PASS

    def set_final_tags(self) -> None:
        pass

    def get_or_create_child(self, name: str) -> t.Tuple[TChildClass, bool]:
        created = False

        if name not in self.children:
            created = True
            child = self.ChildClass(name=name, parent=self)
            child.set_service(self.service)
            self.children[name] = child

        return self.children[name], created

    def set_tags(self, tags: t.Dict[str, str]) -> None:
        self.tags.update(tags)

    def set_metrics(self, metrics: t.Dict[str, float]) -> None:
        self.metrics.update(metrics)


class TestRun(TestItem["Test", t.NoReturn]):
    __test__ = False

    def __init__(self, name: str, parent: Test) -> None:
        super().__init__(name=name, parent=parent)
        self.span_id: t.Optional[int] = None
        self.trace_id: t.Optional[int] = None
        self.attempt_number: int = 0

        self.test = parent
        self.suite = parent.parent
        self.module = self.suite.parent
        self.session = self.module.parent

        self.tags[TestTag.TEST_TYPE] = TestType.TEST

    def __str__(self) -> str:
        return f"{self.test} #{self.attempt_number}"

    def set_context(self, context: TestContext) -> None:
        self.span_id = context.span_id
        self.trace_id = context.trace_id
        self.set_tags(context.get_tags())
        self.set_metrics(context.get_metrics())

    def is_retry(self) -> bool:
        return self.attempt_number > 0

    def has_failed_all_retries(self) -> bool:
        return self.tags.get(TestTag.HAS_FAILED_ALL_RETRIES) == TAG_TRUE

    def mark_benchmark(self) -> None:
        self.tags[TestTag.TEST_TYPE] = TestType.BENCHMARK

    def is_benchmark(self) -> bool:
        return self.tags.get(TestTag.TEST_TYPE) == TestType.BENCHMARK

    # Selenium / RUM functionality. These tags are only available after the test has finished and ddtrace span tags have
    # been copied over to the test run object.
    def is_rum(self) -> bool:
        return self.tags.get(TestTag.IS_RUM_ACTIVE) == TAG_TRUE

    def get_browser_driver(self) -> t.Optional[str]:
        return self.tags.get(TestTag.BROWSER_DRIVER)


class Test(TestItem["TestSuite", "TestRun"]):
    __test__ = False
    ChildClass = TestRun

    def __init__(self, name: str, parent: TestSuite) -> None:
        super().__init__(name=name, parent=parent)

        self.test_runs: t.List[TestRun] = []

        self.suite = parent
        self.module = self.suite.parent
        self.session = self.module.parent

        self._is_flaky_run = False

    def __str__(self) -> str:
        return f"{self.parent.parent.name}/{self.parent.name}::{self.name}"

    def set_attributes(
        self,
        is_new: bool = False,
        is_quarantined: bool = False,
        is_disabled: bool = False,
        is_attempt_to_fix: bool = False,
    ) -> None:
        if is_new:
            self.tags[TestTag.IS_NEW] = TAG_TRUE

        if is_quarantined:
            self.tags[TestTag.IS_QUARANTINED] = TAG_TRUE

        if is_disabled:
            self.tags[TestTag.IS_DISABLED] = TAG_TRUE

        if is_attempt_to_fix:
            self.tags[TestTag.IS_ATTEMPT_TO_FIX] = TAG_TRUE

    def set_location(self, path: t.Union[os.PathLike[t.Any], str], start_line: int) -> None:
        self.tags[TestTag.SOURCE_FILE] = str(path)
        self.metrics[TestTag.SOURCE_START] = start_line

    def get_source_file(self) -> t.Optional[str]:
        return self.tags.get(TestTag.SOURCE_FILE)

    def set_parameters(self, parameters: str) -> None:
        self.tags[TestTag.PARAMETERS] = parameters

    def set_codeowners(self, owners: t.List[str]) -> None:
        self.tags[TestTag.CODEOWNERS] = json.dumps(owners)

    def is_new(self) -> bool:
        return self.tags.get(TestTag.IS_NEW) == TAG_TRUE

    def is_quarantined(self) -> bool:
        return self.tags.get(TestTag.IS_QUARANTINED) == TAG_TRUE

    def is_disabled(self) -> bool:
        return self.tags.get(TestTag.IS_DISABLED) == TAG_TRUE

    def is_attempt_to_fix(self) -> bool:
        return self.tags.get(TestTag.IS_ATTEMPT_TO_FIX) == TAG_TRUE

    def has_parameters(self) -> bool:
        return TestTag.PARAMETERS in self.tags

    def make_test_run(self) -> TestRun:
        test_run = TestRun(name=self.name, parent=self)
        test_run.attempt_number = len(self.test_runs)
        test_run.set_service(self.service)
        self.test_runs.append(test_run)
        return test_run

    @property
    def last_test_run(self) -> TestRun:
        return self.test_runs[-1]

    # ITR tags.

    def mark_unskippable(self) -> None:
        self.tags[TestTag.ITR_UNSKIPPABLE] = TAG_TRUE

    def is_unskippable(self) -> bool:
        return self.tags.get(TestTag.ITR_UNSKIPPABLE) == TAG_TRUE

    def mark_forced_run(self) -> None:
        self.tags[TestTag.ITR_FORCED_RUN] = TAG_TRUE

    def is_forced_run(self) -> bool:
        return self.tags.get(TestTag.ITR_FORCED_RUN) == TAG_TRUE

    def mark_skipped_by_itr(self) -> None:
        self.tags[TestTag.SKIPPED_BY_ITR] = TAG_TRUE
        self.session.tests_skipped_by_itr += 1

    def is_skipped_by_itr(self) -> bool:
        return self.tags.get(TestTag.SKIPPED_BY_ITR) == TAG_TRUE

    # Early Flake Detection.

    def set_early_flake_detection_abort_reason(self, reason: str) -> None:
        self.tags[TestTag.EFD_ABORT_REASON] = reason

    def get_early_flake_detection_abort_reason(self) -> t.Optional[str]:
        return self.tags.get(TestTag.EFD_ABORT_REASON)

    def mark_flaky_run(self) -> None:
        self._is_flaky_run = True

    def is_flaky_run(self) -> bool:
        return self._is_flaky_run


class TestSuite(TestItem["TestModule", "Test"]):
    ChildClass = Test
    __test__ = False

    def __init__(self, name: str, parent: TestModule) -> None:
        super().__init__(name=name, parent=parent)
        self.module = parent
        self.session = self.module.parent

    def __str__(self) -> str:
        return f"{self.parent.name}/{self.name}"


class TestModule(TestItem["TestSession", "TestSuite"]):
    ChildClass = TestSuite
    __test__ = False

    def __init__(self, name: str, parent: TestSession) -> None:
        super().__init__(name=name, parent=parent)
        self.session = parent

    def __str__(self) -> str:
        return f"{self.name}"

    def set_location(self, module_path: Path) -> None:
        self.module_path = str(module_path)


class TestSession(TestItem[t.NoReturn, "TestModule"]):
    ChildClass = TestModule
    __test__ = False

    def __init__(self, name: str):
        super().__init__(name=name, parent=None)  # type: ignore
        self.tests_skipped_by_itr = 0

    def set_session_id(self, session_id: int) -> None:
        self.item_id = session_id

    def set_attributes(self, test_command: str, test_framework: str, test_framework_version: str) -> None:
        self.test_command = test_command
        self.test_framework = test_framework
        self.test_framework_version = test_framework_version

    def set_early_flake_detection_abort_reason(self, reason: str) -> None:
        self.tags[TestTag.EFD_ABORT_REASON] = reason

    def get_early_flake_detection_abort_reason(self) -> t.Optional[str]:
        return self.tags.get(TestTag.EFD_ABORT_REASON)

    def set_final_tags(self) -> None:
        super().set_final_tags()

        if self.tests_skipped_by_itr > 0:
            self.tags[TestTag.ITR_TESTS_SKIPPED] = TAG_TRUE
            self.tags[TestTag.ITR_TESTS_SKIPPING_TYPE] = "test"
            self.metrics[TestTag.ITR_TESTS_SKIPPING_COUNT] = self.tests_skipped_by_itr


class TestTag:
    COMPONENT = "component"
    TEST_COMMAND = "test.command"
    TEST_FRAMEWORK = "test.framework"
    TEST_FRAMEWORK_VERSION = "test.framework_version"
    TEST_SESSION_NAME = "test_session.name"
    TEST_NAME = "test.name"
    TEST_SUITE = "test.suite"

    ENV = "env"

    ERROR_STACK = "error.stack"
    ERROR_TYPE = "error.type"
    ERROR_MESSAGE = "error.message"

    SKIP_REASON = "test.skip_reason"

    TEST_TYPE = "test.type"
    IS_NEW = "test.is_new"
    IS_QUARANTINED = "test.test_management.is_quarantined"
    IS_DISABLED = "test.test_management.is_test_disabled"
    IS_ATTEMPT_TO_FIX = "test.test_management.is_attempt_to_fix"
    ATTEMPT_TO_FIX_PASSED = "test.test_management.attempt_to_fix_passed"
    EFD_ABORT_REASON = "test.early_flake.abort_reason"
    IS_RETRY = "test.is_retry"
    RETRY_REASON = "test.retry_reason"
    HAS_FAILED_ALL_RETRIES = "test.has_failed_all_retries"

    XFAIL_REASON = "pytest.xfail.reason"
    TEST_RESULT = "test.result"  # used for xfail/xpass

    PARAMETERS = "test.parameters"

    ITR_UNSKIPPABLE = "test.itr.unskippable"
    ITR_FORCED_RUN = "test.itr.forced_run"
    SKIPPED_BY_ITR = "test.skipped_by_itr"
    ITR_TESTS_SKIPPED = "test.itr.tests_skipping.tests_skipped"
    ITR_TESTS_SKIPPING_TYPE = "test.itr.tests_skipping.type"
    ITR_TESTS_SKIPPING_COUNT = "test.itr.tests_skipping.count"

    # Test File; used when test implementation file is different from test suite name (pytest-bdd).
    TEST_FILE = "test.file"

    SOURCE_FILE = "test.source.file"
    SOURCE_START = "test.source.start"

    CODEOWNERS = "test.codeowners"

    IS_RUM_ACTIVE = "test.is_rum_active"
    BROWSER_DRIVER = "test.browser.driver"

    CODE_COVERAGE_LINES_PCT = "test.code_coverage.lines_pct"

    __test__ = False
