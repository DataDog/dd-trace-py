from dataclasses import dataclass
import typing as t
from weakref import WeakKeyDictionary as wkdict

from ddtrace import tracer


SessionId = str


def _sessions_from_debug_tag(debug_tag: str) -> t.Generator["Session", None, None]:
    for session in debug_tag.split(","):
        ident, _, level = session.partition(":")
        yield Session(ident=ident, level=int(level or 0))


def _sessions_to_debug_tag(sessions: t.Iterable["Session"]) -> str:
    # TODO: Validate tag length
    return ",".join(f"{session.ident}:{session.level}" for session in sessions)


@dataclass
class Session:
    ident: SessionId
    level: int

    @classmethod
    def activate_distributed(cls, context: t.Any) -> None:
        debug_tag = context._meta.get("_dd.p.debug")
        if debug_tag is None:
            return

        for session in _sessions_from_debug_tag(debug_tag):
            session.link_to_trace(context)

    def propagate(self, context: t.Any) -> None:
        sessions = list(_sessions_from_debug_tag(context))
        for session in sessions:
            if self.ident == session.ident:
                # The session is already in the tags so we don't need to add it
                if self.level > session.level:
                    # We only need to update the level if it's higher
                    session.level = self.level
                break
        else:
            # The session is not in the tags so we need to add it
            sessions.append(self)

        context._meta["_dd.p.debug"] = _sessions_to_debug_tag(sessions)

    def link_to_trace(self, trace_context: t.Optional[t.Any] = None):
        SessionManager.link_session_to_trace(self, trace_context)

    def unlink_from_trace(self, trace_context: t.Optional[t.Any] = None):
        SessionManager.unlink_session_from_trace(self, trace_context)

    @classmethod
    def from_trace(cls) -> t.List["Session"]:
        return SessionManager.get_sessions_for_trace()

    @classmethod
    def lookup(cls, ident: SessionId) -> t.Optional["Session"]:
        return SessionManager.lookup_session(ident)


class SessionManager:
    _sessions_trace_map: t.MutableMapping[
        t.Any, t.Dict[SessionId, Session]
    ] = wkdict()  # Trace context to Sessions mapping

    @classmethod
    def link_session_to_trace(cls, session, trace_context: t.Optional[t.Any] = None) -> None:
        context = trace_context or tracer.current_trace_context()
        if context is None:
            # Nothing to link to
            return

        cls._sessions_trace_map.setdefault(context, {})[session.ident] = session

    @classmethod
    def unlink_session_from_trace(cls, session, trace_context: t.Optional[t.Any] = None) -> None:
        context = trace_context or tracer.current_trace_context()
        if context is None:
            # Nothing to unlink from
            return

        cls._sessions_trace_map.get(context, {}).pop(session.ident, None)

    @classmethod
    def get_sessions_for_trace(cls) -> t.List[Session]:
        context = tracer.current_trace_context()
        if context is None:
            return []

        return list(cls._sessions_trace_map.get(context, {}).values())

    @classmethod
    def lookup_session(cls, ident: SessionId) -> t.Optional[Session]:
        context = tracer.current_trace_context()
        if context is None:
            return None

        return cls._sessions_trace_map.get(context, {}).get(ident)
