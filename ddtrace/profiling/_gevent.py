from types import FrameType
import typing as t

import gevent
from gevent import thread
import gevent.greenlet
from gevent.greenlet import Greenlet as _Greenlet
import gevent.hub
from greenlet import greenlet
from greenlet import settrace

from ddtrace.internal import forksafe
from ddtrace.internal.datadog.profiling import stack
from ddtrace.profiling import _span_links


# Original objects
_gevent_hub_spawn_raw: t.Callable[..., _Greenlet] = gevent.hub.spawn_raw
_gevent_joinall: t.Callable[..., t.Sequence[_Greenlet]] = gevent.joinall
_gevent_wait: t.Callable[..., t.Any] = gevent.wait
_gevent_iwait: t.Callable[..., t.Any] = gevent.iwait

# Global package state
_tracked_greenlets: set[int] = set()
_original_greenlet_tracer: t.Optional[t.Callable[[str, t.Any], None]] = None
_greenlet_parent_map: dict[int, int] = {}
_parent_greenlet_count: dict[int, int] = {}
_is_patched = False

FRAME_NOT_SET: bool = False  # Sentinel for when the frame is not set


def _reset_gevent_state_after_fork() -> None:
    _tracked_greenlets.clear()
    _greenlet_parent_map.clear()
    _parent_greenlet_count.clear()
    if not _is_patched:
        return

    # Restore the surviving execution target before StackCollector's later fork hook republishes its active span.
    try:
        track_gevent_greenlet(gevent.getcurrent(), _from_tracer=True, _seed_context=False)
    except GreenletTrackingError:
        pass


forksafe.register(_reset_gevent_state_after_fork)


def _restart_gevent_tracking() -> None:
    """Invalidate surviving tracking so each greenlet is re-seeded after profiler restart."""
    if not _is_patched:
        return
    for greenlet_id in tuple(_tracked_greenlets):
        _untrack_greenlet_by_id(greenlet_id)
    try:
        track_gevent_greenlet(gevent.getcurrent(), _from_tracer=True)
    except GreenletTrackingError:
        pass


def _current_greenlet_span_target() -> t.Optional[_span_links.LogicalSpanTarget]:
    greenlet_id = t.cast(int, thread.get_ident(gevent.getcurrent()))
    if not stack.is_greenlet_tracked(greenlet_id):
        return None
    return _span_links.LogicalSpanTarget(_span_links.SpanLinkDomain.GEVENT_GREENLET, greenlet_id)


class GreenletTrackingError(Exception):
    """Exception raised when a greenlet cannot be tracked."""

    pass


def track_gevent_greenlet(
    gl: _Greenlet,
    _from_tracer: bool = False,
    _seed_context: bool = True,
) -> _Greenlet:
    greenlet_id: int = thread.get_ident(gl)
    frame: t.Union[FrameType, bool, None] = None if gl is gevent.getcurrent() else FRAME_NOT_SET

    try:
        stack.track_greenlet(greenlet_id, gl.name or type(gl).__qualname__, frame)
    except AttributeError as e:
        raise GreenletTrackingError("Cannot track greenlet with no name attribute") from e
    except Exception as e:
        raise GreenletTrackingError("Cannot track greenlet") from e

    # Set up rawlink for automatic untracking on greenlet completion, but only
    # when called outside the greenlet tracer. Calling rawlink from inside the
    # tracer is unsafe: during a greenlet switch the gevent Greenlet.dead
    # property can incorrectly return True (due to __started_but_aborted()),
    # which causes rawlink to immediately schedule _notify_links. That fires
    # ALL registered callbacks -- including the pool's _discard -- removing the
    # greenlet from the pool while it is still alive.  This breaks gunicorn's
    # graceful-shutdown logic which checks pool.free_count() == pool.size.
    if not _from_tracer:
        try:
            gl.rawlink(untrack_greenlet)
        except AttributeError:
            # This greenlet cannot be linked (e.g. the Hub)
            pass
        except Exception as e:
            raise GreenletTrackingError("Cannot link greenlet for untracking") from e

    _tracked_greenlets.add(greenlet_id)

    try:
        if _seed_context:
            # Read only the tracer configured on the profiler, not the gevent integration's process-global tracer.
            _span_links.link_current_logical_span(_span_links.SpanLinkDomain.GEVENT_GREENLET, greenlet_id)
        elif _from_tracer:
            # A lazily discovered origin may have activated a newer span since its construction Context was captured.
            _span_links.link_logical_span_context(_span_links.SpanLinkDomain.GEVENT_GREENLET, greenlet_id)
    except Exception:  # nosec B110
        pass

    return gl


def record_greenlet_switch(
    origin_id: int,
    origin_frame: t.Union[FrameType, bool, None],
    target_id: int,
    target_frame: t.Union[FrameType, bool, None],
    update_target_frame: bool,
) -> None:
    _tracked_greenlets.add(origin_id)
    if update_target_frame:
        _tracked_greenlets.add(target_id)
    stack.record_greenlet_switch(origin_id, origin_frame, target_id, target_frame, update_target_frame)


def greenlet_tracer(event: str, args: t.Any) -> None:
    # Greenlets that already exist when profiling is enabled are discovered lazily.
    # We only start tracking them once a post-patch "switch"/"throw" event is observed.
    # A greenlet that exits before switching again may not be tracked.
    if event in {"switch", "throw"}:
        # This tracer function runs in the context of the target
        origin, target = t.cast(tuple[_Greenlet, _Greenlet], args)

        if (origin_id := thread.get_ident(origin)) not in _tracked_greenlets:
            try:
                # The origin may already have published a newer activation before lazy discovery. Do not replace it
                # with the tracing Context captured when the greenlet was constructed.
                track_gevent_greenlet(origin, _from_tracer=True, _seed_context=False)
            except GreenletTrackingError:
                # Not something that we can track
                pass

        if (target_id := thread.get_ident(target)) not in _tracked_greenlets:
            # This is likely the hub. We take this chance to track it.
            try:
                track_gevent_greenlet(target, _from_tracer=True)
            except GreenletTrackingError:
                # Not something that we can track
                pass

        try:
            # If this is being set to None, it means the greenlet is likely
            # finished. We use the sentinel again to signal this.
            origin_frame = t.cast(t.Optional[FrameType], origin.gr_frame) or FRAME_NOT_SET
            # We don't want to wipe the frame of a parent greenlet because
            # we need to unwind it. We definitely know it is still running
            # so if we allow the tracer to set its tracked frame to None,
            # we won't be able to unwind the full stack.
            record_greenlet_switch(
                origin_id,
                origin_frame,
                target_id,
                target.gr_frame,  # This is None for the running target.
                target_id not in _parent_greenlet_count,
            )
        except KeyError:
            # TODO: Log missing greenlet
            pass

        # For greenlets tracked via the tracer (without rawlink), detect
        # completion using the C-level greenlet.dead descriptor directly
        # (greenlet.dead.__get__) instead of the gevent Greenlet.dead property.
        #
        # Why this is necessary:
        #   gevent.Greenlet overrides the C-level ``dead`` property and adds an
        #   ``__started_but_aborted()`` check. That check looks at whether
        #   ``_start_event.pending`` is False and ``_start_event`` has not yet
        #   been set to ``_start_completed_event``. During the greenlet bootstrap
        #   phase -- after the event loop consumes the start callback (setting
        #   pending=False) but before ``run()`` sets ``_start_event =
        #   _start_completed_event`` -- this returns a false True, making
        #   gevent's ``Greenlet.dead`` incorrectly report the greenlet as dead.
        #
        #   The C-level ``greenlet.dead`` (``started and not active``) has no
        #   such window: the ``active`` flag is managed by the C stack-switching
        #   machinery and is only cleared when the greenlet truly finishes.
        #
        # See also: https://github.com/gevent/gevent/pull/2166 (upstream fix)
        #
        # AIDEV-NOTE: greenlet.dead.__get__ is a C-level tp_getset descriptor.
        # Any unhandled exception here causes the greenlet runtime to silently
        # uninstall this tracer (see greenlet's TGreenlet.cpp g_calltrace and
        # test_tracing.py::test_b_exception_disables_tracing). We catch
        # Exception broadly because the C extension can raise arbitrary
        # exception types.
        try:
            if origin_id in _tracked_greenlets and greenlet.dead.__get__(origin):
                _untrack_greenlet_by_id(origin_id)
        except Exception:  # nosec B110
            pass

    if _original_greenlet_tracer is not None:
        _original_greenlet_tracer(event, args)


def _untrack_greenlet_by_id(greenlet_id: int) -> None:
    """Untrack a greenlet by its ID. Idempotent."""
    if greenlet_id not in _tracked_greenlets:
        return
    stack.untrack_greenlet(greenlet_id)
    _span_links.clear_logical_span(_span_links.SpanLinkDomain.GEVENT_GREENLET, greenlet_id)
    _tracked_greenlets.discard(greenlet_id)
    _parent_greenlet_count.pop(greenlet_id, None)
    if (parent_id := _greenlet_parent_map.pop(greenlet_id, None)) is not None:
        remaining = _parent_greenlet_count.get(parent_id, 0) - 1
        if remaining <= 0:
            _parent_greenlet_count.pop(parent_id, None)
        else:
            _parent_greenlet_count[parent_id] = remaining


def untrack_greenlet(gl: _Greenlet) -> None:
    _untrack_greenlet_by_id(thread.get_ident(gl))


def link_greenlets(greenlet_id: int, parent_id: int) -> None:
    stack.link_greenlets(greenlet_id, parent_id)
    _parent_greenlet_count[parent_id] = _parent_greenlet_count.get(parent_id, 0) + 1
    _greenlet_parent_map[greenlet_id] = parent_id


class Greenlet(_Greenlet):
    @classmethod
    def spawn(cls, *args: t.Any, **kwargs: t.Any) -> _Greenlet:
        greenlet = super().spawn(*args, **kwargs)
        try:
            return track_gevent_greenlet(greenlet)
        except GreenletTrackingError:
            # If we cannot track the greenlet, we just return it as is.
            return greenlet

    @classmethod
    def spawn_later(cls, *args: t.Any, **kwargs: t.Any) -> _Greenlet:
        greenlet = super().spawn_later(*args, **kwargs)
        try:
            return track_gevent_greenlet(greenlet)
        except GreenletTrackingError:
            return greenlet

    def join(self, *args: t.Any, **kwargs: t.Any) -> None:
        target_id: int = thread.get_ident(self)
        origin_id: int = thread.get_ident(gevent.getcurrent())

        link_greenlets(target_id, origin_id)

        super().join(*args, **kwargs)


def wrap_spawn(original: t.Callable[..., _Greenlet]) -> t.Callable[..., _Greenlet]:
    def _(*args: t.Any, **kwargs: t.Any) -> _Greenlet:
        greenlet = original(*args, **kwargs)
        try:
            return track_gevent_greenlet(greenlet)
        except GreenletTrackingError:
            return greenlet

    return _


def joinall(greenlets: t.Sequence[_Greenlet], *args: t.Any, **kwargs: t.Any) -> t.Sequence[_Greenlet]:
    # This is a wrapper around gevent.joinall to track the greenlets
    # that are being joined.
    current_greenlet = gevent.getcurrent()
    # NOTE: We specifically use `type(...) is ...` here instead of
    # `isinstance`, as gevent.Greenlet inherits from the low level
    # C `greenlet` class, so isinstance would be True for every
    # greenlet type.
    if type(current_greenlet) is greenlet:
        current_greenlet = gevent.hub.get_hub()
    current_greenlet_id: int = thread.get_ident(current_greenlet)
    for g in greenlets:
        link_greenlets(thread.get_ident(g), current_greenlet_id)
    return _gevent_joinall(greenlets, *args, **kwargs)


def wait_wrapper(original: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
    def _(*args: t.Any, **kwargs: t.Any) -> t.Any:
        try:
            objects = args[0]
        except IndexError:
            objects = kwargs.get("objects")

        if objects is None:
            objects = []

        if greenlets := [_ for _ in objects if isinstance(_, (greenlet, gevent.Greenlet))]:
            current_greenlet = gevent.getcurrent()
            if type(current_greenlet) is greenlet:
                current_greenlet = gevent.hub.get_hub()
            current_greenlet_id: int = thread.get_ident(current_greenlet)
            for g in greenlets:
                link_greenlets(thread.get_ident(g), current_greenlet_id)

        return original(*args, **kwargs)

    return _


def get_current_greenlet_task() -> tuple[t.Optional[int], t.Optional[str], t.Optional[FrameType]]:
    current_greenlet = gevent.getcurrent()
    task_id = thread.get_ident(current_greenlet)
    # Import locally to avoid eager import order interactions.
    from ddtrace.profiling import _threading

    task_name = _threading.get_thread_name(task_id)
    frame = t.cast(t.Optional[FrameType], current_greenlet.gr_frame)
    return task_id, task_name, frame


def patch() -> None:
    global _is_patched, _original_greenlet_tracer

    # Patch the spawn method to track greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = Greenlet
    gevent.spawn = Greenlet.spawn
    gevent.spawn_later = Greenlet.spawn_later
    gevent.joinall = joinall
    gevent.wait = wait_wrapper(_gevent_wait)
    gevent.iwait = wait_wrapper(_gevent_iwait)

    gevent.hub.spawn_raw = wrap_spawn(_gevent_hub_spawn_raw)

    _original_greenlet_tracer = t.cast(t.Callable[[str, t.Any], None], settrace(greenlet_tracer))
    _span_links.register_logical_span_provider(_current_greenlet_span_target, priority=10)
    _is_patched = True


def unpatch() -> None:
    global _is_patched

    _is_patched = False
    # Stop routing activation events before restoring the original greenlet hooks.
    _span_links.unregister_logical_span_provider(_current_greenlet_span_target)
    for greenlet_id in tuple(_tracked_greenlets):
        _untrack_greenlet_by_id(greenlet_id)

    # Unpatch the spawn method to stop tracking greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = _Greenlet
    gevent.spawn = _Greenlet.spawn
    gevent.spawn_later = _Greenlet.spawn_later
    gevent.joinall = _gevent_joinall
    gevent.wait = _gevent_wait
    gevent.iwait = _gevent_iwait

    gevent.hub.spawn_raw = _gevent_hub_spawn_raw

    settrace(_original_greenlet_tracer)
