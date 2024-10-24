from dataclasses import dataclass
import typing as t
from weakref import WeakKeyDictionary as wkdict

from ddtrace import tracer


SessionId = str


@dataclass
class Session:
    ident: SessionId
    level: int

    def link_to_trace(self, trace_context: t.Optional[t.Any] = None):
        SessionManager.link_session_to_trace(self, trace_context)

    @classmethod
    def from_trace(cls) -> t.Iterable["Session"]:
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
    def get_sessions_for_trace(cls) -> t.Iterable[Session]:
        context = tracer.current_trace_context()
        if context is None:
            return []

        return cls._sessions_trace_map.get(context, {}).values()

    @classmethod
    def lookup_session(cls, ident: SessionId) -> t.Optional[Session]:
        context = tracer.current_trace_context()
        if context is None:
            return None

        return cls._sessions_trace_map.get(context, {}).get(ident)
