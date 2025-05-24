import atexit
import dataclasses
import enum
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.settings._config import config


_listeners: Dict[str, Dict[Any, Callable[..., Any]]] = {}
_all_listeners: List[Callable[[str, Tuple[Any, ...]], None]] = []


class ResultType(enum.Enum):
    RESULT_OK = 0
    RESULT_EXCEPTION = 1
    RESULT_UNDEFINED = -1


@dataclasses.dataclass
class EventResult:
    response_type: ResultType = ResultType.RESULT_UNDEFINED
    value: Any = None
    exception: Optional[Exception] = None

    def __bool__(self):
        "EventResult can easily be checked as a valid result"
        return self.response_type == ResultType.RESULT_OK


_MissingEvent = EventResult()


class EventResultDict(Dict[str, EventResult]):
    def __missing__(self, key: str) -> EventResult:
        return _MissingEvent

    def __getattr__(self, name: str) -> EventResult:
        return dict.__getitem__(self, name)


_MissingEventDict = EventResultDict()


def has_listeners(event_id: str) -> bool:
    """Check if there are hooks registered for the provided event_id"""
    global _listeners
    return bool(_listeners.get(event_id))


def on(event_id: str, callback: Callable[..., Any], name: Any = None) -> None:
    """Register a listener for the provided event_id"""
    global _listeners
    if name is None:
        name = id(callback)
    if event_id not in _listeners:
        _listeners[event_id] = {}
    _listeners[event_id][name] = callback


def on_all(callback: Callable[..., Any]) -> None:
    """Register a listener for all events emitted"""
    global _all_listeners
    if callback not in _all_listeners:
        _all_listeners.insert(0, callback)


def reset(event_id: Optional[str] = None, callback: Optional[Callable[..., Any]] = None) -> None:
    """Remove all registered listeners. If an event_id is provided, only clear those
    event listeners. If a callback is provided, then only the listeners for that callback are removed.
    """
    global _listeners
    global _all_listeners

    if callback:
        if not event_id:
            _all_listeners = [cb for cb in _all_listeners if cb != callback]
        elif event_id in _listeners:
            _listeners[event_id] = {name: cb for name, cb in _listeners[event_id].items() if cb != callback}
    else:
        if not event_id:
            _listeners.clear()
            _all_listeners.clear()
        elif event_id in _listeners:
            del _listeners[event_id]


def dispatch(event_id: str, args: Tuple[Any, ...] = ()) -> None:
    """Call all hooks for the provided event_id with the provided args"""
    if os.getenv("PYTEST_XDIST_WORKER") and event_id.startswith("test_visibility."):
        return remote_dispatch(event_id, args)

    global _all_listeners
    global _listeners

    for hook in _all_listeners:
        try:
            hook(event_id, args)
        except Exception:
            if config._raise:
                raise

    if event_id not in _listeners:
        return

    for local_hook in _listeners[event_id].values():
        try:
            local_hook(*args)
        except Exception:
            if config._raise:
                raise


def dispatch_with_results(event_id: str, args: Tuple[Any, ...] = ()) -> EventResultDict:
    """Call all hooks for the provided event_id with the provided args
    returning the results and exceptions from the called hooks
    """
    if os.getenv("PYTEST_XDIST_WORKER") and event_id.startswith("test_visibility."):
        return remote_dispatch_with_results(event_id, args)

    global _listeners
    global _all_listeners

    for hook in _all_listeners:
        try:
            hook(event_id, args)
        except Exception:
            if config._raise:
                raise

    if event_id not in _listeners:
        return _MissingEventDict

    results = EventResultDict()
    for name, hook in _listeners[event_id].items():
        try:
            results[name] = EventResult(ResultType.RESULT_OK, hook(*args))
        except Exception as e:
            if config._raise:
                raise
            results[name] = EventResult(ResultType.RESULT_EXCEPTION, None, e)

    return results



import pickle
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen
from ddtrace.internal import core

DD_CORE_HOST = "localhost"
DD_CORE_PORT = 41414


class DDCoreRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('content-length'))
        body = self.rfile.read(length)
        args = pickle.loads(body)

        if self.path == "/dispatch":
            method = core.dispatch
        elif self.path == "/dispatch_with_results":
            method = core.dispatch_with_results
        else:
            raise Exception("unknown core method")

        #print("REQUEST:", args, flush=True)
        result = method(*args)
        if isinstance(result, EventResultDict):
            result = {**result} # avoid mysterious "object is not callable" error from pickle.dumps
        #print("RESULT:", result, flush=True)
        body = pickle.dumps(result)

        self.wfile.write(b"HTTP/1.0 200 OK\r\n")
        self.wfile.write(b"content-length: " + (str(len(body)).encode('utf-8')) + b"\r\n\r\n")
        self.wfile.write(body)

class DDCoreServer:
    server = None

    @classmethod
    def start(cls):
        if os.getenv("PYTEST_XDIST_WORKER"):
            return
        print("ꙮ Starting core event hub server ꙮ")
        def core_server_thread():
            cls.server = HTTPServer((DD_CORE_HOST, DD_CORE_PORT), DDCoreRequestHandler)
            cls.server.allow_reuse_address = True
            cls.server.serve_forever()
        threading.Thread(target=core_server_thread).start()

    @classmethod
    def stop(cls):
        if os.getenv("PYTEST_XDIST_WORKER"):
            return
        print("ꙮ Stopping core event hub server ꙮ")
        cls.server.shutdown()


def remote_dispatch(*args):
    if args[0] in ["test_visibility.enable", "test_visibility.disable"]:
        return
    print("ꙮ Remote dispatch: {args}", flush=True)
    response = urlopen(f"http://{DD_CORE_HOST}:{DD_CORE_PORT}/dispatch", data=pickle.dumps(args))
    result = pickle.loads(response.read())
    print("ꙮ Remote dispatch result: {result}")
    return result

def remote_dispatch_with_results(*args):
    print("ꙮ Remote dispatch_with_results: {args}", flush=True)
    response = urlopen(f"http://{DD_CORE_HOST}:{DD_CORE_PORT}/dispatch_with_results", data=pickle.dumps(args))
    result = EventResultDict(pickle.loads(response.read()))
    print("ꙮ Remote dispatch_with_results result: {result}")
    return result
