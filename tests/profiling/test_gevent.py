"""Tests for ddtrace/profiling/_gevent.py task-linkage helpers."""

import os
import sys

import pytest


GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess()
def test_untrack_parent_before_child_no_keyerror() -> None:
    """Untracking a parent greenlet before its linked child must not raise KeyError.

    When _untrack_greenlet_by_id(parent) is called, _parent_greenlet_count[parent]
    is popped. If the child is later untracked and tries to decrement that entry,
    a KeyError was raised. This test verifies the fix.
    """
    from unittest.mock import patch

    from ddtrace.profiling import _gevent as _gevent_module

    saved_tracked = set(_gevent_module._tracked_greenlets)
    saved_count = dict(_gevent_module._parent_greenlet_count)
    saved_map = dict(_gevent_module._greenlet_parent_map)

    try:
        with patch.object(_gevent_module, "stack"):
            parent_id, child_id = 100001, 100002
            _gevent_module._tracked_greenlets.update({parent_id, child_id})
            _gevent_module.link_greenlets(child_id, parent_id)

            assert _gevent_module._parent_greenlet_count.get(parent_id) == 1

            _gevent_module._untrack_greenlet_by_id(parent_id)

            assert parent_id not in _gevent_module._tracked_greenlets
            assert parent_id not in _gevent_module._parent_greenlet_count

            _gevent_module._untrack_greenlet_by_id(child_id)

            assert child_id not in _gevent_module._tracked_greenlets
            assert child_id not in _gevent_module._greenlet_parent_map
            assert parent_id not in _gevent_module._parent_greenlet_count
    finally:
        _gevent_module._tracked_greenlets.clear()
        _gevent_module._tracked_greenlets.update(saved_tracked)
        _gevent_module._parent_greenlet_count.clear()
        _gevent_module._parent_greenlet_count.update(saved_count)
        _gevent_module._greenlet_parent_map.clear()
        _gevent_module._greenlet_parent_map.update(saved_map)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess()
def test_untrack_parent_before_child_no_keyerror_real_greenlets() -> None:
    """Regression test: no KeyError when a timed-out joiner is untracked before the joined greenlet.

    When a greenlet calls .join() with a Timeout, the joiner can exit before
    the joined greenlet finishes. This causes _untrack_greenlet_by_id(joiner)
    to pop joiner's _parent_greenlet_count entry, after which untracking the
    joined greenlet would raise KeyError trying to decrement that entry.
    """
    from unittest.mock import patch

    import gevent
    import gevent.monkey

    gevent.monkey.patch_all()

    from ddtrace.profiling import _gevent as _gevent_module
    from ddtrace.profiling._gevent import Greenlet

    caught: list[Exception] = []
    original = _gevent_module._untrack_greenlet_by_id

    def _spy(greenlet_id: int) -> None:
        try:
            original(greenlet_id)
        except KeyError as e:
            caught.append(e)
            raise

    with patch.object(_gevent_module, "_untrack_greenlet_by_id", _spy):

        def slow_task() -> None:
            gevent.sleep(0.5)

        def joiner() -> None:
            slow = Greenlet.spawn(slow_task)
            try:
                with gevent.Timeout(0.05):
                    slow.join()
            except gevent.Timeout:
                pass

        g = Greenlet.spawn(joiner)
        g.join()
        gevent.sleep(1.0)

    assert not caught, f"KeyError raised during untrack: {caught[0]}"


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess()
def test_joinall_links_to_calling_greenlet_not_hub() -> None:
    """joinall must link joined greenlets to the *calling* Greenlet, not the Hub.

    Previously ``isinstance(current_greenlet, greenlet)`` was used as the guard,
    which is True for ALL gevent Greenlets (they inherit from greenlet), so the
    parent was always replaced with the Hub regardless of which greenlet called
    joinall.
    """
    from unittest.mock import patch

    import gevent
    from gevent import thread

    from ddtrace.profiling import _gevent as _gevent_module

    links: list[tuple[int, int]] = []

    def _capture_link(child_id: int, parent_id: int) -> None:
        links.append((child_id, parent_id))

    child_greenlet = gevent.spawn(gevent.sleep, 1000)
    try:
        result: list[tuple[int, int]] = []

        def caller() -> None:
            caller_id = thread.get_ident(gevent.getcurrent())
            with patch.object(_gevent_module, "link_greenlets", side_effect=_capture_link):
                with patch.object(_gevent_module, "_gevent_joinall", return_value=[]):
                    _gevent_module.joinall([child_greenlet])
            result.append((caller_id, thread.get_ident(child_greenlet)))

        caller_greenlet = gevent.spawn(caller)
        caller_greenlet.join(timeout=5)

        assert result, "caller greenlet did not finish"
        caller_id, child_id = result[0]
        assert links, "link_greenlets was never called"
        linked_child, linked_parent = links[0]
        assert linked_child == child_id
        assert linked_parent == caller_id, (
            f"Expected parent={caller_id} (calling Greenlet), got parent={linked_parent} "
            f"(Hub id={thread.get_ident(gevent.hub.get_hub())})"
        )
    finally:
        child_greenlet.kill()


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess()
def test_wait_wrapper_links_via_keyword_objects_arg() -> None:
    """wait_wrapper must detect greenlets passed as the ``objects`` keyword argument.

    Previously the fallback used ``kwargs.get("args", [])`` instead of
    ``kwargs.get("objects", [])``, so calling ``gevent.wait(objects=[...])``
    silently skipped all greenlet linking.
    """
    from unittest.mock import patch

    import gevent
    from gevent import thread

    from ddtrace.profiling import _gevent as _gevent_module

    links: list[tuple[int, int]] = []

    def _capture_link(child_id: int, parent_id: int) -> None:
        links.append((child_id, parent_id))

    child_greenlet = gevent.spawn(gevent.sleep, 1000)
    try:
        with patch.object(_gevent_module, "link_greenlets", side_effect=_capture_link):
            wrapped = _gevent_module.wait_wrapper(lambda *a, **kw: None)
            wrapped(objects=[child_greenlet])

        assert links, "link_greenlets was never called when 'objects' passed as keyword argument"
        linked_child, _ = links[0]
        assert linked_child == thread.get_ident(child_greenlet)
    finally:
        child_greenlet.kill()


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess()
def test_wait_wrapper_links_to_calling_greenlet_not_hub() -> None:
    """wait_wrapper must link to the *calling* Greenlet, not the Hub."""
    from unittest.mock import patch

    import gevent
    from gevent import thread

    from ddtrace.profiling import _gevent as _gevent_module

    links: list[tuple[int, int]] = []

    def _capture_link(child_id: int, parent_id: int) -> None:
        links.append((child_id, parent_id))

    child_greenlet = gevent.spawn(gevent.sleep, 1000)
    try:
        result: list[tuple[int, int]] = []

        def caller() -> None:
            caller_id = thread.get_ident(gevent.getcurrent())
            with patch.object(_gevent_module, "link_greenlets", side_effect=_capture_link):
                wrapped = _gevent_module.wait_wrapper(lambda *a, **kw: None)
                wrapped([child_greenlet])
            result.append((caller_id, thread.get_ident(child_greenlet)))

        caller_greenlet = gevent.spawn(caller)
        caller_greenlet.join(timeout=5)

        assert result, "caller greenlet did not finish"
        caller_id, child_id = result[0]
        assert links, "link_greenlets was never called"
        linked_child, linked_parent = links[0]
        assert linked_child == child_id
        assert linked_parent == caller_id, (
            f"Expected parent={caller_id} (calling Greenlet), got parent={linked_parent} "
            f"(Hub id={thread.get_ident(gevent.hub.get_hub())})"
        )
    finally:
        child_greenlet.kill()
