import ctypes
import logging
import sys
from types import FrameType
import typing as t

import gevent
from gevent import thread
import gevent.greenlet
from gevent.greenlet import Greenlet as _Greenlet
import gevent.hub
from greenlet import greenlet
from greenlet import settrace

from ddtrace.internal.datadog.profiling import stack


log = logging.getLogger(__name__)

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

# When set, the sampler reads gr_frame and stack_stop directly from
# greenlet memory at sample time (~100Hz).  This eliminates ALL per-switch
# C calls from the greenlet tracer.
# Tuple of (pimpl_offset, frame_offset, stack_stop_offset).
_greenlet_frame_offsets: t.Optional[tuple[int, int, int]] = None

FRAME_NOT_SET: bool = False  # Sentinel for when the frame is not set


class GreenletTrackingError(Exception):
    """Exception raised when a greenlet cannot be tracked."""

    pass


def track_gevent_greenlet(gl: _Greenlet, _from_tracer: bool = False) -> _Greenlet:
    greenlet_id: int = thread.get_ident(gl)
    frame: t.Union[FrameType, bool, None] = FRAME_NOT_SET

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

    return gl


def _discover_greenlet_offsets() -> tuple[int, int, int]:
    """Discover byte offsets to read gr_frame and stack_stop from greenlet memory.

    Since greenlet 2.0, the PyGreenlet struct uses a pimpl pattern where all
    real state is behind a void* pointer to a C++ Greenlet object:

        PyGreenlet + pimpl_offset       -> Greenlet* (heap-allocated impl)
        Greenlet*  + frame_offset       -> struct _frame* (the gr_frame value)
        Greenlet*  + stack_stop_offset  -> char* (non-NULL when started)

    Phase 1 creates a paused probe greenlet and scans its raw memory to find
    the two-hop path to the frame pointer.

    Phase 2 snapshots pimpl memory from a running greenlet, compares with
    the paused and unstarted states to find stack_stop (non-NULL for all
    started greenlets, NULL for unstarted).  This lets the sampler
    distinguish "on-CPU" (NULL frame + started) from "never ran" at sample
    time.

    Returns (pimpl_offset, frame_offset, stack_stop_offset).
    Raises RuntimeError if offsets cannot be determined.
    """
    ptr_size = ctypes.sizeof(ctypes.c_void_p)
    sub_size = 512

    def _read_ptr(buf: ctypes.Array[ctypes.c_char], offset: int) -> int:
        """Read a pointer-sized integer from a ctypes buffer at the given offset."""
        return int.from_bytes(buf[offset : offset + ptr_size], sys.byteorder)  # type: ignore[arg-type]

    def _probe_fn() -> None:
        greenlet.getcurrent().parent.switch()

    # ---- Phase 1: discover pimpl_offset and frame_offset ----

    g = greenlet(_probe_fn)
    g.switch()  # g is now paused with a real gr_frame

    frame = g.gr_frame
    if frame is None:
        raise RuntimeError("Probe greenlet has no frame")

    frame_addr = id(frame)
    gl_size = sys.getsizeof(g)
    type_addr = id(type(g))

    buf = (ctypes.c_char * gl_size).from_address(id(g))

    pimpl_offset = None
    frame_offset = None

    for p_off in range(0, gl_size - ptr_size + 1, ptr_size):
        pimpl_ptr = _read_ptr(buf, p_off)
        if pimpl_ptr == 0 or pimpl_ptr < 0x1000 or pimpl_ptr == type_addr:
            continue

        try:
            pimpl_buf = (ctypes.c_char * sub_size).from_address(pimpl_ptr)
            for f_off in range(0, sub_size - ptr_size + 1, ptr_size):
                if _read_ptr(pimpl_buf, f_off) != frame_addr:
                    continue

                # Validate with a second greenlet
                g2 = greenlet(_probe_fn)
                g2.switch()
                frame2 = g2.gr_frame
                if frame2 is None:
                    continue

                buf2 = (ctypes.c_char * gl_size).from_address(id(g2))
                pimpl2 = _read_ptr(buf2, p_off)
                if pimpl2 == 0:
                    continue

                pimpl_buf2 = (ctypes.c_char * sub_size).from_address(pimpl2)
                if _read_ptr(pimpl_buf2, f_off) == id(frame2):
                    pimpl_offset = p_off
                    frame_offset = f_off
                    break
        except (ValueError, OSError):
            continue
        if pimpl_offset is not None:
            break

    if pimpl_offset is None or frame_offset is None:
        raise RuntimeError("Could not discover greenlet frame offset")

    # ---- Phase 2: discover stack_stop_offset ----
    # stack_stop is non-NULL for all started greenlets (both active and
    # paused) and NULL for unstarted ones.  This lets us distinguish
    # "on-CPU" (NULL frame + started) from "never started" (NULL frame +
    # not started) at sample time.
    #
    # Discovery: snapshot the pimpl memory from inside a running greenlet,
    # compare with the paused state and an unstarted greenlet.  stack_stop
    # is the field that is non-NULL in all three cases (active, paused) but
    # NULL for unstarted, AND has the same value in active and paused states.

    def _stack_probe_fn() -> None:
        me = greenlet.getcurrent()
        me_buf = (ctypes.c_char * gl_size).from_address(id(me))
        my_pimpl = _read_ptr(me_buf, pimpl_offset)
        my_sub = (ctypes.c_char * sub_size).from_address(my_pimpl)

        snapshot = {}
        for off in range(0, sub_size - ptr_size + 1, ptr_size):
            snapshot[off] = _read_ptr(my_sub, off)

        me.parent.switch(snapshot)

    g3 = greenlet(_stack_probe_fn)
    active_snapshot = g3.switch()  # g3 is now paused

    buf3 = (ctypes.c_char * gl_size).from_address(id(g3))
    pimpl3 = _read_ptr(buf3, pimpl_offset)
    paused_sub = (ctypes.c_char * sub_size).from_address(pimpl3)

    g4 = greenlet(lambda: None)  # unstarted
    buf4 = (ctypes.c_char * gl_size).from_address(id(g4))
    pimpl4 = _read_ptr(buf4, pimpl_offset)

    candidates = []
    if pimpl4 != 0:
        try:
            unstarted_sub = (ctypes.c_char * sub_size).from_address(pimpl4)
            for off in range(0, sub_size - ptr_size + 1, ptr_size):
                if off == frame_offset:
                    continue
                active_val = active_snapshot.get(off, 0)
                paused_val = _read_ptr(paused_sub, off)
                unstarted_val = _read_ptr(unstarted_sub, off)

                # stack_stop: non-NULL for started (active AND paused), NULL for unstarted,
                # and stable (same value) across active/paused transitions.
                if active_val != 0 and active_val == paused_val and unstarted_val == 0:
                    candidates.append(off)
        except (ValueError, OSError):
            pass

    if not candidates:
        raise RuntimeError("Could not discover greenlet stack_stop offset")

    stack_stop_offset = candidates[0]

    return (pimpl_offset, frame_offset, stack_stop_offset)


def update_greenlet_frame(greenlet_id: int, frame: t.Union[FrameType, bool, None]) -> None:
    _tracked_greenlets.add(greenlet_id)
    stack.update_greenlet_frame(greenlet_id, frame)


def greenlet_tracer(event: str, args: t.Any) -> None:
    # Greenlets that already exist when profiling is enabled are discovered lazily.
    # We only start tracking them once a post-patch "switch"/"throw" event is observed.
    # A greenlet that exits before switching again may not be tracked.
    if event in {"switch", "throw"}:
        # This tracer function runs in the context of the target
        origin, target = t.cast(tuple[_Greenlet, _Greenlet], args)

        if (origin_id := thread.get_ident(origin)) not in _tracked_greenlets:
            try:
                track_gevent_greenlet(origin, _from_tracer=True)
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

        if not _greenlet_frame_offsets:
            # Fallback: cache frames per-switch for the sampler to read.
            # Only used when offset discovery failed at init time.
            try:
                update_greenlet_frame(
                    origin_id,
                    t.cast(t.Optional[FrameType], origin.gr_frame) or FRAME_NOT_SET,
                )
                if target_id not in _parent_greenlet_count:
                    update_greenlet_frame(target_id, target.gr_frame)
            except KeyError:
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
    global _original_greenlet_tracer, _greenlet_frame_offsets

    # Discover greenlet struct offsets so the sampler can read gr_frame and
    # stack_stop directly from greenlet memory at sample time, eliminating
    # ALL per-switch C calls from the tracer.  Falls back to per-switch
    # frame caching if discovery fails (e.g. unsupported greenlet version).
    try:
        offsets = _discover_greenlet_offsets()
        stack.set_greenlet_offsets(*offsets)
        _greenlet_frame_offsets = offsets
        log.debug(
            "Greenlet offsets discovered: pimpl=%d, frame=%d, stack_stop=%d",
            offsets[0],
            offsets[1],
            offsets[2],
        )
    except Exception:
        _greenlet_frame_offsets = None
        log.debug("Greenlet offset discovery failed, using per-switch fallback", exc_info=True)

    # Patch the spawn method to track greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = Greenlet
    gevent.spawn = Greenlet.spawn
    gevent.spawn_later = Greenlet.spawn_later
    gevent.joinall = joinall
    gevent.wait = wait_wrapper(_gevent_wait)
    gevent.iwait = wait_wrapper(_gevent_iwait)

    gevent.hub.spawn_raw = wrap_spawn(_gevent_hub_spawn_raw)

    if _greenlet_frame_offsets:
        # Offsets available: the sampler reads frames directly from greenlet
        # memory at sample time.  No settrace callback needed, so zero
        # Python code runs per greenlet switch.  Greenlet tracking relies
        # on the patched spawn/joinall above; dead detection uses rawlink.
        pass
    else:
        # Fallback: install settrace for per-switch frame caching and
        # lazy tracking of pre-existing greenlets.
        _original_greenlet_tracer = t.cast(t.Callable[[str, t.Any], None], settrace(greenlet_tracer))


def unpatch() -> None:
    global _greenlet_frame_offsets

    # Unpatch the spawn method to stop tracking greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = _Greenlet
    gevent.spawn = _Greenlet.spawn
    gevent.spawn_later = _Greenlet.spawn_later
    gevent.joinall = _gevent_joinall
    gevent.wait = _gevent_wait
    gevent.iwait = _gevent_iwait

    gevent.hub.spawn_raw = _gevent_hub_spawn_raw

    if not _greenlet_frame_offsets:
        settrace(_original_greenlet_tracer)
    _greenlet_frame_offsets = None
