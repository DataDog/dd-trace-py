import os
import threading
import time
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.aiguard import aiguard_config


log = get_logger(__name__)

requires: list[str] = []

_x509_lock = threading.Lock()
_x509_client = None
_x509_watcher_stop: Optional[threading.Event] = None
_x509_watcher_thread: Optional[threading.Thread] = None
_x509_pid: Optional[int] = None


def get_x509_client():
    return _x509_client


def _collect_host_tags() -> list[str]:
    tags: list[str] = []
    for key, env_var in (("env", "DD_ENV"), ("service", "DD_SERVICE"), ("version", "DD_VERSION")):
        val = os.environ.get(env_var, "").strip()
        if val:
            tags.append(f"{key}:{val}")
    return tags


def _create_client_with_defer_retry(
    site: str,
    api_key: str,
    register_url: Optional[str],
    host_tags: list[str],
    stop_event: threading.Event,
):
    import ddtrace
    from ddtrace.internal.native import AiGuardX509Client
    from ddtrace.internal.native_runtime import get_native_runtime

    app_version = getattr(ddtrace, "__version__", "0.0.0")
    runtime = get_native_runtime()

    deadline = time.monotonic() + 5.0
    while not stop_event.is_set():
        try:
            return AiGuardX509Client(
                runtime,
                site=site,
                api_key=api_key,
                app_name="dd-trace-py",
                app_version=app_version,
                register_url=register_url,
                host_tags=host_tags,
            )
        except RuntimeError as exc:
            if "deferred" in str(exc) and time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            raise
    return None


def _watch_x509(
    client,
    site: str,
    api_key: str,
    register_url: Optional[str],
    host_tags: list[str],
    stop_event: threading.Event,
    pid: int,
) -> None:
    global _x509_client

    if client is None:
        try:
            client = _create_client_with_defer_retry(site, api_key, register_url, host_tags, stop_event)
        except Exception:
            log.warning("[ai-guard-x509] pid=%d failed to start x509 client after fork", pid, exc_info=True)
            return
        if client is None or stop_event.is_set():
            if client is not None:
                client.shutdown()
            return
        with _x509_lock:
            if _x509_pid == pid and _x509_watcher_stop is stop_event:
                _x509_client = client
            else:
                client.shutdown()
                return
        log.info("[ai-guard-x509] pid=%d started after fork (site=%s)", pid, site)

    last_cid: Optional[str] = None
    connected_at: Optional[float] = None
    first_payload_logged = False
    while not stop_event.is_set():
        cid = client.connection_id()
        if cid and cid != last_cid:
            last_cid = cid
            connected_at = time.monotonic()
            first_payload_logged = False
            log.info("[ai-guard-x509] pid=%d connected connection_id=%s", pid, cid)
            print(f"[ai-guard-x509] pid={pid} connected connection_id={cid}", flush=True)

        received = client.wait_for_payload(timeout_ms=500)
        if received is not None:
            reason, payload = received
            latency_note = ""
            if connected_at is not None and not first_payload_logged:
                first_payload_logged = True
                latency_ms = (time.monotonic() - connected_at) * 1000.0
                latency_note = f" connect_to_push_ms={latency_ms:.1f}"
            log.info(
                "[ai-guard-x509] pid=%d received payload reason=%r payload=%r%s",
                pid,
                reason,
                payload,
                latency_note,
            )
            print(
                f"[ai-guard-x509] pid={pid} received payload reason={reason!r} payload={payload!r}{latency_note}",
                flush=True,
            )


def _start_x509_client(restarting_after_fork: bool = False) -> None:
    global _x509_client, _x509_watcher_stop, _x509_watcher_thread, _x509_pid

    if not aiguard_config._ai_guard_x509_enabled:
        return

    api_key = os.environ.get("DD_API_KEY", "").strip()
    if not api_key:
        log.warning("[ai-guard-x509] DD_AI_GUARD_X509_ENABLED is set but DD_API_KEY is missing")
        return

    site = os.environ.get("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
    register_url = (aiguard_config._ai_guard_x509_register_url or "").strip() or None
    host_tags = _collect_host_tags()

    with _x509_lock:
        current_pid = os.getpid()
        if not restarting_after_fork and _x509_client is not None and _x509_pid == current_pid:
            return

        if _x509_watcher_stop is not None:
            _x509_watcher_stop.set()
        if not restarting_after_fork and _x509_client is not None:
            try:
                _x509_client.shutdown()
            except Exception:
                log.debug("[ai-guard-x509] error shutting down previous client", exc_info=True)
        _x509_client = None
        _x509_watcher_thread = None

        stop_event = threading.Event()
        client = None
        if not restarting_after_fork:
            try:
                client = _create_client_with_defer_retry(site, api_key, register_url, host_tags, stop_event)
            except Exception:
                log.warning("[ai-guard-x509] failed to start x509 client", exc_info=True)
                return

        watcher = threading.Thread(
            target=_watch_x509,
            args=(client, site, api_key, register_url, host_tags, stop_event, current_pid),
            name=f"dd-ai-guard-x509-{current_pid}",
            daemon=True,
        )
        _x509_client = client
        _x509_watcher_stop = stop_event
        _x509_watcher_thread = watcher
        _x509_pid = current_pid
        watcher.start()
        if client is not None:
            log.info("[ai-guard-x509] pid=%d started (site=%s register_url=%s)", current_pid, site, register_url)


def _stop_x509_client(join: bool = False) -> None:
    global _x509_client, _x509_watcher_stop, _x509_watcher_thread, _x509_pid
    with _x509_lock:
        if _x509_watcher_stop is not None:
            _x509_watcher_stop.set()
        client = _x509_client
        thread = _x509_watcher_thread
        _x509_client = None
        _x509_watcher_stop = None
        _x509_watcher_thread = None
        _x509_pid = None

    if client is not None:
        try:
            client.shutdown()
        except Exception:
            log.debug("[ai-guard-x509] error shutting down client", exc_info=True)
    if join and thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(aiguard_config._ai_guard_enabled or aiguard_config._ai_guard_x509_enabled)


def start() -> None:
    from ddtrace.aiguard._initialization import load_ai_guard

    load_ai_guard()
    _start_x509_client(restarting_after_fork=False)


def restart(join: bool = False) -> None:
    _start_x509_client(restarting_after_fork=True)


def stop(join: bool = False) -> None:
    _stop_x509_client(join=join)
