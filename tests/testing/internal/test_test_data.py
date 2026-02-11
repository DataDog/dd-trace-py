"""Tests for ddtrace.testing.internal.test_data module."""

from typing import Any
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestItem
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestStatus


class TestImmutableRef:
    """Tests for NamedTuple-based ref types (immutability of ModuleRef, SuiteRef, TestRef)."""

    def test_module_ref_immutable_raises_attribute_error(self) -> None:
        """Test that assigning to ModuleRef raises AttributeError."""
        module = ModuleRef(name="test_module")
        with pytest.raises(AttributeError, match="can't set attribute"):
            module.name = "new_name"  # type: ignore[misc]

    def test_suite_ref_immutable_raises_attribute_error(self) -> None:
        """Test that assigning to SuiteRef raises AttributeError."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")
        with pytest.raises(AttributeError, match="can't set attribute"):
            suite.name = "new_name"  # type: ignore[misc]

    def test_test_ref_immutable_raises_attribute_error(self) -> None:
        """Test that assigning to TestRef raises AttributeError."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")
        test = TestRef(suite=suite, name="test_function")
        with pytest.raises(AttributeError, match="can't set attribute"):
            test.name = "new_name"  # type: ignore[misc]


class TestModuleRef:
    """Tests for ModuleRef (NamedTuple-based immutable ref)."""

    def test_module_ref_creation(self) -> None:
        """Test that ModuleRef can be created with a name."""
        module = ModuleRef(name="test_module")
        assert module.name == "test_module"

    def test_module_ref_immutable(self) -> None:
        """Test that ModuleRef is immutable."""
        module = ModuleRef(name="test_module")
        with pytest.raises(AttributeError):
            module.name = "new_name"  # type: ignore[misc]

    def test_module_ref_equality(self) -> None:
        """Test ModuleRef equality based on name."""
        module1 = ModuleRef(name="test_module")
        module2 = ModuleRef(name="test_module")
        module3 = ModuleRef(name="different_module")

        assert module1 == module2
        assert module1 != module3

    def test_module_ref_hashable_and_dedup_in_set(self) -> None:
        """Test that equal ModuleRefs have same hash and deduplicate in a set."""
        module1 = ModuleRef(name="mod")
        module2 = ModuleRef(name="mod")
        assert module1 == module2
        assert hash(module1) == hash(module2)
        assert len({module1, module2}) == 1


class TestSuiteRef:
    """Tests for SuiteRef (NamedTuple-based immutable ref)."""

    def test_suite_ref_creation(self) -> None:
        """Test that SuiteRef can be created with module and name."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")

        assert suite.module == module
        assert suite.name == "test_suite"

    def test_suite_ref_immutable(self) -> None:
        """Test that SuiteRef is immutable."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")

        with pytest.raises(AttributeError):
            suite.name = "new_name"  # type: ignore[misc]

    def test_suite_ref_equality(self) -> None:
        """Test SuiteRef equality based on module and name."""
        mod = ModuleRef(name="mod")
        suite1 = SuiteRef(module=mod, name="suite")
        suite2 = SuiteRef(module=mod, name="suite")
        suite3 = SuiteRef(module=mod, name="other")

        assert suite1 == suite2
        assert suite1 != suite3

    def test_suite_ref_hashable_and_dedup_in_set(self) -> None:
        """Test that equal SuiteRefs have same hash and deduplicate in a set."""
        mod = ModuleRef(name="mod")
        suite1 = SuiteRef(module=mod, name="suite")
        suite2 = SuiteRef(module=mod, name="suite")
        assert suite1 == suite2
        assert hash(suite1) == hash(suite2)
        assert len({suite1, suite2}) == 1


class TestTestRef:
    """Tests for TestRef (NamedTuple-based immutable ref)."""

    def test_test_ref_creation(self) -> None:
        """Test that TestRef can be created with suite and name."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")
        test = TestRef(suite=suite, name="test_function")

        assert test.suite == suite
        assert test.name == "test_function"

    def test_test_ref_immutable(self) -> None:
        """Test that TestRef is immutable."""
        module = ModuleRef(name="test_module")
        suite = SuiteRef(module=module, name="test_suite")
        test = TestRef(suite=suite, name="test_function")

        with pytest.raises(AttributeError):
            test.name = "new_name"  # type: ignore[misc]

    def test_test_ref_equality(self) -> None:
        """Test TestRef equality based on suite and name."""
        mod = ModuleRef(name="mod")
        suite = SuiteRef(module=mod, name="suite")
        ref1 = TestRef(suite=suite, name="test")
        ref2 = TestRef(suite=suite, name="test")
        ref3 = TestRef(suite=suite, name="other")

        assert ref1 == ref2
        assert ref1 != ref3

    def test_test_ref_hashable_and_dedup_in_set(self) -> None:
        """Test that equal TestRefs have same hash and deduplicate in a set."""
        mod = ModuleRef(name="mod")
        suite = SuiteRef(module=mod, name="suite")
        ref1 = TestRef(suite=suite, name="test")
        ref2 = TestRef(suite=suite, name="test")
        assert ref1 == ref2
        assert hash(ref1) == hash(ref2)
        assert len({ref1, ref2}) == 1

    def test_test_ref_as_dict_key(self) -> None:
        """Test that TestRef can be used as dict key and lookup works."""
        mod = ModuleRef(name="mod")
        suite = SuiteRef(module=mod, name="suite")
        ref1 = TestRef(suite=suite, name="test")
        ref2 = TestRef(suite=suite, name="test")
        d = {ref1: "value"}
        assert d[ref2] == "value"


class TestTestStatus:
    """Tests for TestStatus enum."""

    def test_test_status_values(self) -> None:
        """Test that TestStatus enum has correct values."""
        assert TestStatus.PASS.value == "pass"
        assert TestStatus.FAIL.value == "fail"
        assert TestStatus.SKIP.value == "skip"

    def test_test_status_comparison(self) -> None:
        """Test TestStatus enum comparison."""
        assert TestStatus.PASS == TestStatus.PASS
        # These comparisons are intentionally checking enum inequality
        assert TestStatus.PASS != TestStatus.FAIL  # type: ignore[comparison-overlap]
        assert TestStatus.FAIL != TestStatus.SKIP  # type: ignore[comparison-overlap]


class MockTestItem(TestItem[Any, Any]):
    """Mock TestItem for testing."""

    ChildClass = Any

    def __init__(self, name: str, parent: Any = None) -> None:
        super().__init__(name, parent)


class TestTestItem:
    """Tests for TestItem class."""

    def test_test_item_creation(self) -> None:
        """Test that TestItem can be created with name and parent."""
        parent = MockTestItem(name="parent")
        item = MockTestItem(name="test_item", parent=parent)

        assert item.name == "test_item"
        assert item.parent == parent
        assert item.children == {}
        assert item.start_ns is None
        assert item.duration_ns is None
        assert item.status is None
        assert item.tags == {}
        assert item.metrics == {}
        assert item.service == DEFAULT_SERVICE_NAME
        assert isinstance(item.item_id, int)

    def test_test_item_start_with_default_time(self) -> None:
        """Test TestItem.start() with default time."""
        item = MockTestItem(name="test_item")

        with patch("ddtrace.internal.utils.time.Time.time_ns", return_value=1000000000):
            item.start()

        assert item.start_ns == 1000000000

    def test_test_item_start_with_custom_time(self) -> None:
        """Test TestItem.start() with custom time."""
        item = MockTestItem(name="test_item")
        custom_time = 2000000000

        item.start(start_ns=custom_time)
        assert item.start_ns == custom_time

    def test_ensure_started_when_not_started(self) -> None:
        """Test ensure_started() when item hasn't been started."""
        item = MockTestItem(name="test_item")
        assert item.start_ns is None

        with patch("ddtrace.internal.utils.time.Time.time_ns", return_value=1000000000):
            item.ensure_started()

        assert item.start_ns == 1000000000

    def test_ensure_started_when_already_started(self) -> None:
        """Test ensure_started() when item is already started."""
        item = MockTestItem(name="test_item")
        item.start_ns = 500000000

        item.ensure_started()
        assert item.start_ns == 500000000  # Should remain unchanged

    def test_finish(self) -> None:
        """Test TestItem.finish() method."""
        item = MockTestItem(name="test_item")
        item.start_ns = 1000000000

        with patch("ddtrace.internal.utils.time.Time.time_ns", return_value=2000000000):
            item.finish()

        assert item.duration_ns == 1000000000  # 2000000000 - 1000000000

    def test_is_finished(self) -> None:
        """Test TestItem.is_finished() method."""
        item = MockTestItem(name="test_item")
        assert not item.is_finished()

        item.duration_ns = 1000000000
        assert item.is_finished()

    def test_seconds_so_far(self) -> None:
        """Test TestItem.seconds_so_far() method."""
        item = MockTestItem(name="test_item")
        item.start_ns = 1000000000

        with patch("ddtrace.internal.utils.time.Time.time_ns", return_value=3000000000):
            seconds = item.seconds_so_far()

        assert seconds == 2.0  # (3000000000 - 1000000000) / 1e9

    def test_set_status(self) -> None:
        """Test TestItem.set_status() method."""
        item = MockTestItem(name="test_item")

        item.set_status(TestStatus.PASS)
        assert item.status == TestStatus.PASS

        item.set_status(TestStatus.FAIL)
        assert item.status == TestStatus.FAIL  # type: ignore[comparison-overlap]

    def test_get_status_explicit(self) -> None:
        """Test TestItem.get_status() when status is explicitly set."""
        item = MockTestItem(name="test_item")
        item.set_status(TestStatus.PASS)

        assert item.get_status() == TestStatus.PASS

    def test_set_service(self) -> None:
        """Test TestItem.set_service() method."""
        item = MockTestItem(name="test_item")
        assert item.service == DEFAULT_SERVICE_NAME

        item.set_service("custom_service")
        assert item.service == "custom_service"

    def test_get_status_from_children_no_children(self) -> None:
        """Test _get_status_from_children when there are no children."""
        item = MockTestItem(name="test_item")

        # When no children, total_count is 0, and status_counts[SKIP] is also 0, so 0 == 0 returns SKIP
        status = item._get_status_from_children()
        assert status == TestStatus.SKIP

    def test_get_status_from_children_with_fail(self) -> None:
        """Test _get_status_from_children when children have failures."""
        parent = MockTestItem(name="parent")
        child1 = MockTestItem(name="child1", parent=parent)
        child2 = MockTestItem(name="child2", parent=parent)

        child1.set_status(TestStatus.PASS)
        child2.set_status(TestStatus.FAIL)

        parent.children["child1"] = child1
        parent.children["child2"] = child2

        status = parent._get_status_from_children()
        assert status == TestStatus.FAIL

    def test_get_status_from_children_all_skip(self) -> None:
        """Test _get_status_from_children when all children are skipped."""
        parent = MockTestItem(name="parent")
        child1 = MockTestItem(name="child1", parent=parent)
        child2 = MockTestItem(name="child2", parent=parent)

        child1.set_status(TestStatus.SKIP)
        child2.set_status(TestStatus.SKIP)

        parent.children["child1"] = child1
        parent.children["child2"] = child2

        status = parent._get_status_from_children()
        assert status == TestStatus.SKIP

    def test_get_status_from_children_mixed_pass_skip(self) -> None:
        """Test _get_status_from_children with mix of pass and skip."""
        parent = MockTestItem(name="parent")
        child1 = MockTestItem(name="child1", parent=parent)
        child2 = MockTestItem(name="child2", parent=parent)

        child1.set_status(TestStatus.PASS)
        child2.set_status(TestStatus.SKIP)

        parent.children["child1"] = child1
        parent.children["child2"] = child2

        status = parent._get_status_from_children()
        assert status == TestStatus.PASS
