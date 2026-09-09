from contextlib import ExitStack
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from queue import Queue
import socket
from threading import Thread
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import SignalQueue
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.debugging._uploader import SignalUploaderError
from ddtrace.debugging._uploader import UploaderProduct
from ddtrace.internal.native import DebuggerSender
from ddtrace.internal.native import DebuggerSenderError
from ddtrace.internal.native import DebuggerTrackType
from ddtrace.internal.native_runtime import get_native_runtime


# DEV: Using float('inf') with lock wait intervals may cause an OverflowError
# so we use a large enough integer as an approximation instead.
LONG_INTERVAL = 2147483647.0

# httpretty -- reached through tests.internal.remoteconfig.rcm_endpoint, which
# test_debugger.py uses earlier in this suite -- intermittently leaves the socket
# module holding its fakes after being disabled. A server built on a fake socket binds
# a port that never accepts, so the native sender only reports a timeout. These are
# captured at import time, before any test can patch them, and reinstalled for the
# lifetime of the intake server below.
_REAL_SOCKET_ATTRS = {
    name: getattr(socket, name) for name in ("socket", "create_connection", "getaddrinfo", "socketpair")
}


class MockSignalUploader(SignalUploader):
    def __init__(self, *args, **kwargs):
        super(MockSignalUploader, self).__init__(*args, **kwargs)
        self.queue = Queue()
        self._state = self._online

    def _write(self, payload, debugger_type):
        self.queue.put(payload.decode())

    @property
    def payloads(self):
        return [json.loads(data) for data in self.queue]


class ActiveBatchJsonEncoder(MockSignalUploader):
    def __init__(self, size=1 << 10, interval=1):
        super(ActiveBatchJsonEncoder, self).__init__(interval)

        # Override the signal queue
        for track in self._tracks.values():
            track.queue = SignalQueue(None, size, self.on_full)

    def on_full(self, item, encoded):
        self.periodic()


def test_uploader_batching():
    with ActiveBatchJsonEncoder(interval=LONG_INTERVAL) as uploader:
        queue = uploader._tracks.values().__iter__().__next__().queue
        for _ in range(5):
            queue.put_encoded(None, "hello".encode("utf-8"))
            queue.put_encoded(None, "world".encode("utf-8"))
            uploader.periodic()

        for _ in range(5):
            assert uploader.queue.get(timeout=1) == "[hello,world]", "iteration %d" % _


def test_uploader_full_buffer():
    size = 1 << 8
    with ActiveBatchJsonEncoder(size=size, interval=LONG_INTERVAL) as uploader:
        item = "hello" * 10
        n = size // len(item)
        assert n

        with pytest.raises(BufferFull):
            queue = uploader._tracks.values().__iter__().__next__().queue
            for _ in range(2 * n):
                queue.put_encoded(None, item.encode("utf-8"))

        # The full buffer forces a flush
        uploader.queue.get(timeout=0.5)
        assert uploader.queue.qsize() == 0

        # wakeup to mimic next interval
        uploader.periodic()
        assert uploader.queue.qsize() == 0


def test_uploader_502_error():
    """Test that _write raises SignalUploaderError when the payload is rejected."""
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender = _make_sender(rejected=(502, "Bad Gateway"))

    # Assert that 502 errors raise SignalUploaderError
    with pytest.raises(SignalUploaderError):
        uploader._write(b'{"test": "data"}', DebuggerTrackType.Logs)


def test_info_check_endpoint_selection():
    """info_check picks the track endpoints, checked by where payloads actually land."""
    with _intake_server() as intake:
        # v2 advertised (without a leading slash): both tracks use v2.
        uploader = _uploader_to(intake.url)
        assert uploader.info_check({"endpoints": ["debugger/v2/input", "debugger/v1/diagnostics"]}) is True
        assert uploader._tracks[SignalTrack.LOGS].enabled is True
        assert uploader._tracks[SignalTrack.SNAPSHOT].enabled is True
        assert intake.path_for(uploader, DebuggerTrackType.Logs) == "/debugger/v2/input"
        assert intake.path_for(uploader, DebuggerTrackType.Snapshots) == "/debugger/v2/input"

        # Only diagnostics advertised (leading-slash form): both tracks fall back.
        uploader = _uploader_to(intake.url)
        assert uploader.info_check({"endpoints": ["/debugger/v1/diagnostics"]}) is True
        assert intake.path_for(uploader, DebuggerTrackType.Logs) == "/debugger/v1/diagnostics"
        assert intake.path_for(uploader, DebuggerTrackType.Snapshots) == "/debugger/v1/diagnostics"

        # An agent that regains the v2 endpoint undoes an earlier downgrade.
        uploader = _uploader_to(intake.url)
        assert uploader._sender.downgrade_to_diagnostics() is True
        assert intake.path_for(uploader, DebuggerTrackType.Logs) == "/debugger/v1/diagnostics"
        assert uploader.info_check({"endpoints": ["debugger/v2/input"]}) is True
        assert intake.path_for(uploader, DebuggerTrackType.Logs) == "/debugger/v2/input"

        # The diagnostics track is unaffected by the downgrade: it is already there.
        uploader = _uploader_to(intake.url)
        assert uploader.info_check({"endpoints": ["debugger/v2/input"]}) is True
        assert intake.path_for(uploader, DebuggerTrackType.Diagnostics) == "/debugger/v1/diagnostics"

    # No supported endpoints: both tracks disabled.
    uploader = SignalUploader(interval=LONG_INTERVAL)
    assert uploader.info_check({"endpoints": ["/some/other/endpoint"]}) is True
    assert uploader._tracks[SignalTrack.LOGS].enabled is False
    assert uploader._tracks[SignalTrack.SNAPSHOT].enabled is False

    # No endpoints key, or agent unreachable: returns False.
    uploader = SignalUploader(interval=LONG_INTERVAL)
    assert uploader.info_check({"version": "7.48.0"}) is False
    assert uploader.info_check(None) is False


def test_downgrade_to_diagnostics_is_idempotent():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    assert uploader._sender.downgrade_to_diagnostics() is True
    # A second downgrade is a no-op, which is what stops _flush_track from
    # retrying the same endpoint forever.
    assert uploader._sender.downgrade_to_diagnostics() is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _IntakeServer:
    """A real HTTP server standing in for the agent, recording the paths it is POSTed to.

    Endpoint selection lives in the native sender, so the only way to observe it
    from Python is to send something and see where it lands.
    """

    def __init__(self) -> None:
        paths = self.paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                # The sender streams debugger payloads chunked; drain either
                # framing so the connection is not left half-read.
                if self.headers.get("Transfer-Encoding") == "chunked":
                    while True:
                        size = int(self.rfile.readline().strip(), 16)
                        if not size:
                            self.rfile.readline()
                            break
                        self.rfile.read(size)
                        self.rfile.readline()
                else:
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))

                paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def path_for(self, uploader, track) -> str:
        """POST an empty batch on `track` and return the path it landed on."""
        del self.paths[:]
        # Send through the sender rather than uploader._write, which swallows
        # transport failures: an unreachable server has to fail the test with the
        # reason, not look indistinguishable from "no request was made".
        response = uploader._sender.send(b"[]", track)
        assert response.accepted, f"intake rejected the payload: {response.status} {response.body}"
        (path,) = self.paths
        # The sender always appends a ddtags query string; only the route matters.
        return path.partition("?")[0]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@contextmanager
def _intake_server():
    # socket.socket is consulted again on every accept(), so the real
    # implementations have to stay installed for as long as the server serves, not
    # just while it is constructed.
    with ExitStack() as stack:
        for name, real in _REAL_SOCKET_ATTRS.items():
            stack.enter_context(patch.object(socket, name, real))

        server = _IntakeServer()
        try:
            yield server
        finally:
            server.close()


def _uploader_to(url):
    """An uploader whose sender posts to `url` instead of the configured agent."""
    sender = DebuggerSender(get_native_runtime(), url=url, tags="")
    with patch("ddtrace.debugging._uploader.build_debugger_sender", return_value=sender):
        return SignalUploader(interval=LONG_INTERVAL)


def _make_sender(rejected=None, error=None, agentless=False):
    """A stand-in for the native DebuggerSender.

    ``rejected`` is the ``(status, body)`` the send should report as a rejection,
    ``error`` an exception it should raise instead.
    """
    sender = MagicMock()
    sender.agentless = agentless
    if error is not None:
        sender.send.side_effect = error
    else:
        response = MagicMock()
        if rejected is None:
            response.accepted = True
            response.status = None
            response.body = ""
        else:
            response.accepted = False
            response.status, response.body = rejected
        sender.send.return_value = response
    return sender


def _put_data(uploader, track=SignalTrack.LOGS, data=b"data"):
    uploader._tracks[track].queue.put_encoded(None, data)


def test_write_success_meters():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender = _make_sender()

    with patch("ddtrace.debugging._uploader.meter") as mock_meter:
        uploader._write(b"payload", DebuggerTrackType.Logs)

    mock_meter.increment.assert_called_with("upload.success")
    mock_meter.distribution.assert_called_with("upload.size", len(b"payload"))


def test_write_rejection_meters_status():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender = _make_sender(rejected=(404, "Not Found"))

    with patch("ddtrace.debugging._uploader.meter") as mock_meter, pytest.raises(SignalUploaderError):
        uploader._write(b"payload", DebuggerTrackType.Snapshots)

    mock_meter.increment.assert_called_with("upload.error", tags={"status": "404"})


def test_write_connection_exception_is_caught():
    # A request that never completed is dropped rather than raised: there is no
    # endpoint to fall back to, unlike a rejection.
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender = _make_sender(error=DebuggerSenderError("connection refused"))

    with patch("ddtrace.debugging._uploader.meter") as mock_meter, patch("ddtrace.debugging._uploader.log") as mock_log:
        uploader._write(b"payload", DebuggerTrackType.Logs)

    mock_meter.increment.assert_called_with("error")
    mock_log.error.assert_called_once()


def test_on_buffer_full_sets_flag_and_wakes():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    with patch.object(uploader, "upload") as mock_upload:
        uploader._on_buffer_full(None, b"")

    assert uploader._flush_full is True
    mock_upload.assert_called_once()


def test_upload_calls_awake():
    uploader = SignalUploader(interval=LONG_INTERVAL)

    with patch.object(uploader, "awake") as mock_awake:
        uploader.upload()

    mock_awake.assert_called_once()


def test_reset_replaces_queues_and_updates_collector():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    old_queues = {t: ut.queue for t, ut in uploader._tracks.items()}

    uploader.reset()

    for track, ut in uploader._tracks.items():
        assert ut.queue is not old_queues[track], "queue should be a new instance after reset"
        assert uploader._collector._tracks[track] is ut.queue


def test_flush_track_downgrades_and_retries_on_signal_uploader_error():
    with _intake_server() as intake:
        uploader = _uploader_to(intake.url)
        _put_data(uploader)

        calls = []

        def write_side_effect(payload, debugger_type):
            calls.append(debugger_type)
            if len(calls) == 1:
                raise SignalUploaderError("first attempt")

        with (
            patch.object(uploader, "_write_with_backoff", side_effect=write_side_effect),
            patch("ddtrace.debugging._uploader.meter"),
        ):
            uploader._flush_track(uploader._tracks[SignalTrack.LOGS])

        # The retry goes to the same track; it is the sender that now resolves it
        # to the diagnostics endpoint.
        assert calls == [DebuggerTrackType.Logs, DebuggerTrackType.Logs]
        assert intake.path_for(uploader, DebuggerTrackType.Logs) == "/debugger/v1/diagnostics"


def test_flush_track_reraises_when_already_on_diagnostics():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender.downgrade_to_diagnostics()
    _put_data(uploader)

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=SignalUploaderError("still failing")),
        pytest.raises(SignalUploaderError),
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])


def test_flush_track_swallows_rejection_when_agentless():
    # Agentless has no fallback endpoint and no agent to re-negotiate with, so a
    # rejection must not bubble up and flip the service back to agent checking.
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._sender = _make_sender(agentless=True)
    uploader._sender.downgrade_to_diagnostics.return_value = False
    _put_data(uploader)

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=SignalUploaderError("rejected")),
        patch("ddtrace.debugging._uploader.log") as mock_log,
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])

    mock_log.debug.assert_called_once()


def test_agentless_uploader_starts_online_and_skips_info():
    from ddtrace.internal import agent as agent_module

    with patch("ddtrace.debugging._uploader.build_debugger_sender", return_value=_make_sender(agentless=True)):
        uploader = SignalUploader(interval=LONG_INTERVAL)

    assert uploader._state == uploader._online

    # Nothing to negotiate: /info is never polled, and info_check passes even
    # when the agent is unreachable.
    with patch.object(agent_module, "info") as mock_info:
        assert uploader.info_check(None) is True
    mock_info.assert_not_called()


def test_flush_track_swallows_generic_exception():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    _put_data(uploader)

    with (
        patch.object(uploader, "_write_with_backoff", side_effect=RuntimeError("oops")),
        patch("ddtrace.debugging._uploader.log") as mock_log,
    ):
        uploader._flush_track(uploader._tracks[SignalTrack.LOGS])

    mock_log.debug.assert_called_once()


def test_online_flushes_full_track():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._flush_full = True

    # Fill the queue so is_full() is True
    track = uploader._tracks[SignalTrack.LOGS]
    while not track.queue.is_full():
        try:
            track.queue.put_encoded(None, b"x" * 256)
        except BufferFull:
            break

    flushed = []
    original_flush_track = uploader._flush_track

    def _capturing_flush_track(t):
        flushed.append(t)
        # avoid actual HTTP
        with patch.object(uploader, "_write_with_backoff"):
            original_flush_track(t)

    with patch.object(uploader, "_flush_track", side_effect=_capturing_flush_track):
        try:
            uploader.online()
        except ValueError:
            pass  # tracks-not-enabled; irrelevant here

    assert any(t.track == SignalTrack.LOGS for t in flushed)
    assert uploader._flush_full is False


def test_online_raises_when_tracks_disabled():
    uploader = SignalUploader(interval=LONG_INTERVAL)
    uploader._tracks[SignalTrack.LOGS].enabled = False
    uploader._tracks[SignalTrack.SNAPSHOT].enabled = False

    with pytest.raises(ValueError, match="not enabled"):
        uploader.online()


def test_agent_check_is_throttled():
    # A missing/unsupported agent must not cause /info to be polled on every
    # periodic tick; the check is throttled independently of the upload interval.
    from ddtrace.internal import agent as agent_module

    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["/some/other/endpoint"]}

    with patch.object(agent_module, "info", return_value=agent_info) as mock_info:
        uploader._agent_check()
        uploader._agent_check()

    assert mock_info.call_count == 1
    assert uploader._state == uploader._agent_check


def test_unreachable_agent_is_not_throttled():
    # A transient /info failure (agent restart/startup race) must not suppress
    # capability checks; only a confirmed unsupported agent is throttled.
    from ddtrace.internal import agent as agent_module

    uploader = SignalUploader(interval=LONG_INTERVAL)

    with patch.object(agent_module, "info", return_value=None) as mock_info:
        uploader._agent_check()
        uploader._agent_check()

    assert mock_info.call_count == 2
    assert uploader._agent_check_throttle.trickling() is False


def test_agent_check_recovers_after_online_failure():
    # A transient upload failure after a healthy online cycle must not be
    # throttled: the next tick should re-check the agent immediately.
    from ddtrace.internal import agent as agent_module

    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["debugger/v2/input"]}

    with patch.object(agent_module, "info", return_value=agent_info) as mock_info:
        # Healthy cycle: go online and clear the throttle.
        uploader._agent_check()
        assert uploader._state == uploader._online
        assert mock_info.call_count == 1

        # Simulate a transient upload failure reverting us to the agent check.
        with patch.object(uploader, "online", side_effect=ValueError("transient")):
            uploader._online()
        assert uploader._state == uploader._agent_check

        # Recovery must happen on the next tick, not after AGENT_CHECK_INTERVAL.
        uploader._agent_check()
        assert mock_info.call_count == 2
        assert uploader._state == uploader._online


def test_agent_check_throttle_reset_on_fork():
    # A forked child must not inherit the parent's throttle window.
    from ddtrace.internal import agent as agent_module

    uploader = SignalUploader(interval=LONG_INTERVAL)
    agent_info = {"endpoints": ["/some/other/endpoint"]}

    with patch.object(agent_module, "info", return_value=agent_info):
        uploader._agent_check()
    assert uploader._agent_check_throttle.trickling() is True

    uploader.reset()
    assert uploader._agent_check_throttle.trickling() is False


class _IsolatedUploader(MockSignalUploader):
    """Subclass to isolate class-level _products/_instance state from production code."""

    _instance = None
    _products: set = set()


def test_register_ignores_duplicate():
    try:
        _IsolatedUploader.register(UploaderProduct.DEBUGGER)
        instance_after_first = _IsolatedUploader._instance

        _IsolatedUploader.register(UploaderProduct.DEBUGGER)
        # Instance must not be replaced on second registration
        assert _IsolatedUploader._instance is instance_after_first
    finally:
        _IsolatedUploader.unregister(UploaderProduct.DEBUGGER)


def test_unregister_ignores_unknown_product():
    # Should not raise, and should not change _instance
    _IsolatedUploader._instance = None
    _IsolatedUploader._products = set()

    _IsolatedUploader.unregister(UploaderProduct.EXCEPTION_REPLAY)  # was never registered

    assert _IsolatedUploader._instance is None
