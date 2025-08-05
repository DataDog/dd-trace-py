from types import FrameType
import typing as t

import gevent
from gevent import thread
import gevent.greenlet
from gevent.greenlet import Greenlet as _Greenlet
import gevent.hub
from greenlet import greenlet
from greenlet import settrace

from ddtrace.internal.datadog.profiling import stack_v2


# Original objects
_gevent_hub_spawn_raw = gevent.hub.spawn_raw
_gevent_joinall = gevent.joinall
_gevent_wait = gevent.wait
_gevent_iwait = gevent.iwait

# Global package state
_greenlet_frames: t.Dict[int, t.Union[FrameType, bool, None]] = {}
_original_greenlet_tracer: t.Optional[t.Callable[[str, t.Any], None]] = None
_greenlet_parent_map: t.Dict[int, int] = {}
_parent_greenlet_count: t.Dict[int, int] = {}

FRAME_NOT_SET = False  # Sentinel for when the frame is not set


class GreenletTrackingError(Exception):
    """Exception raised when a greenlet cannot be tracked."""

    pass


def track_gevent_greenlet(greenlet: _Greenlet) -> _Greenlet:
    greenlet_id = thread.get_ident(greenlet)
    frame: t.Union[FrameType, bool, None] = FRAME_NOT_SET

    try:
        stack_v2.track_greenlet(greenlet_id, greenlet.name or type(greenlet).__qualname__, frame)  # type: ignore[attr-defined]
    except AttributeError as e:
        raise GreenletTrackingError("Cannot track greenlet with no name attribute") from e
    except Exception as e:
        raise GreenletTrackingError("Cannot track greenlet") from e

    # Untrack on completion
    try:
        greenlet.rawlink(untrack_greenlet)
    except AttributeError:
        # This greenlet cannot be linked (e.g. the Hub)
        pass
    except Exception as e:
        raise GreenletTrackingError("Cannot link greenlet for untracking") from e

    _greenlet_frames[greenlet_id] = frame

    return greenlet


def update_greenlet_frame(greenlet_id: int, frame: t.Union[FrameType, bool, None]) -> None:
    _greenlet_frames[greenlet_id] = frame
    stack_v2.update_greenlet_frame(greenlet_id, frame)  # type: ignore[attr-defined]


def greenlet_tracer(event: str, args: t.Any) -> None:
    if event in {"switch", "throw"}:
        # This tracer function runs in the context of the target
        origin, target = t.cast(t.Tuple[Greenlet, Greenlet], args)

        if (origin_id := thread.get_ident(origin)) not in _greenlet_frames:
            try:
                track_gevent_greenlet(origin)
            except GreenletTrackingError:
                # Not something that we can track
                pass

        if (target_id := thread.get_ident(target)) not in _greenlet_frames:
            # This is likely the hub. We take this chance to track it.
            try:
                track_gevent_greenlet(target)
            except GreenletTrackingError:
                # Not something that we can track
                pass

        try:
            # If this is being set to None, it means the greenlet is likely
            # finished. We use the sentinel again to signal this.
            update_greenlet_frame(
                origin_id,
                t.cast(t.Optional[FrameType], origin.gr_frame) or FRAME_NOT_SET,
            )
            if target_id not in _parent_greenlet_count:
                # We don't want to wipe the frame of a parent greenlet because
                # we need to unwind it. We definitely know it is still running
                # so if we allow the tracer to set its tracked frame to None,
                # we won't be able to unwind the full stack.
                update_greenlet_frame(target_id, target.gr_frame)  # this *is* None
        except KeyError:
            # TODO: Log missing greenlet
            pass

    if _original_greenlet_tracer is not None:
        _original_greenlet_tracer(event, args)


def untrack_greenlet(greenlet: _Greenlet) -> None:
    greenlet_id = thread.get_ident(greenlet)
    stack_v2.untrack_greenlet(greenlet_id)  # type: ignore[attr-defined]
    _greenlet_frames.pop(greenlet_id, None)
    _parent_greenlet_count.pop(greenlet_id, None)
    if (parent_id := _greenlet_parent_map.pop(greenlet_id, None)) is not None:
        _parent_greenlet_count[parent_id] -= 1
        if _parent_greenlet_count[parent_id] <= 0:
            del _parent_greenlet_count[parent_id]


def link_greenlets(greenlet_id: int, parent_id: int) -> None:
    stack_v2.link_greenlets(greenlet_id, parent_id)  # type: ignore[attr-defined]
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
        target_id = thread.get_ident(self)
        origin_id = thread.get_ident(gevent.getcurrent())

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


def joinall(greenlets: t.Iterable[_Greenlet], *args: t.Any, **kwargs: t.Any) -> None:
    # This is a wrapper around gevent.joinall to track the greenlets
    # that are being joined.
    current_greenlet = gevent.getcurrent()
    if isinstance(current_greenlet, greenlet):
        current_greenlet = gevent.hub.get_hub()
    current_greenlet_id = thread.get_ident(current_greenlet)
    for g in greenlets:
        link_greenlets(thread.get_ident(g), current_greenlet_id)
    _gevent_joinall(greenlets, *args, **kwargs)


def wait_wrapper(original: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
    def _(*args: t.Any, **kwargs: t.Any) -> t.Any:
        try:
            objects = args[0]
        except IndexError:
            objects = kwargs.get("args", [])

        if greenlets := [_ for _ in objects if isinstance(_, (greenlet, gevent.Greenlet))]:
            current_greenlet = gevent.getcurrent()
            if isinstance(current_greenlet, greenlet):
                current_greenlet = gevent.hub.get_hub()
            current_greenlet_id = thread.get_ident(current_greenlet)
            for g in greenlets:
                link_greenlets(thread.get_ident(g), current_greenlet_id)

        return original(*args, **kwargs)

    return _


def patch() -> None:
    global _original_greenlet_tracer

    # Patch the spawn method to track greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = Greenlet
    gevent.spawn = Greenlet.spawn
    gevent.spawn_later = Greenlet.spawn_later
    gevent.joinall = joinall
    gevent.wait = wait_wrapper(_gevent_wait)
    gevent.iwait = wait_wrapper(_gevent_iwait)

    gevent.hub.spawn_raw = wrap_spawn(_gevent_hub_spawn_raw)

    _original_greenlet_tracer = settrace(greenlet_tracer)


def unpatch() -> None:
    # Unpatch the spawn method to stop tracking greenlets.
    gevent.Greenlet = gevent.greenlet.Greenlet = _Greenlet
    gevent.spawn = _Greenlet.spawn
    gevent.spawn_later = _Greenlet.spawn_later
    gevent.joinall = _gevent_joinall
    gevent.wait = _gevent_wait
    gevent.iwait = _gevent_iwait

    gevent.hub.spawn_raw = _gevent_hub_spawn_raw

    settrace(_original_greenlet_tracer)
