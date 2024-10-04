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
        if trace_context is None:
            # Get the current root
            trace_context = tracer.current_root_span()

        if trace_context is None:
            # We don't have a context to link to
            return

        # If the root has a parent context, use that
        try:
            trace_context = trace_context.context or trace_context
        except AttributeError:
            pass

        cls._sessions_trace_map.setdefault(trace_context, {})[session.ident] = session

    @classmethod
    def get_sessions_for_trace(cls) -> t.Iterable[Session]:
        root = tracer.current_root_span()
        if root is None:
            return []

        return cls._sessions_trace_map.get(root.context or root, {}).values()

    @classmethod
    def lookup_session(cls, ident: SessionId) -> t.Optional[Session]:
        root = tracer.current_root_span()
        if root is None:
            return None

        return cls._sessions_trace_map.get(root.context or root, {}).get(ident)  # type: ignore[call-overload]
