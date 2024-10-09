from dataclasses import dataclass
import typing as t
from weakref import WeakKeyDictionary as wkdict

from ddtrace import tracer


@dataclass
class Session:
    ident: str
    level: int

    def link_to_trace(self, trace_context: t.Optional[t.Any] = None):
        SessionManager.link_session_to_trace(self, trace_context)

    @classmethod
    def get_for_trace(cls) -> t.Optional["Session"]:
        return SessionManager.get_session_for_trace()


class SessionManager:
    _session_trace_map: wkdict = wkdict()  # Trace context to Session mapping

    @classmethod
    def link_session_to_trace(cls, session, trace_context: t.Optional[t.Any] = None) -> None:
        cls._session_trace_map[trace_context or tracer.current_root_span()] = session

    @classmethod
    def get_session_for_trace(cls) -> t.Optional[Session]:
        root = tracer.current_root_span()
        if root is None:
            return None

        return cls._session_trace_map.get(root.context or root)
