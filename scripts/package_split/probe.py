"""Fresh-subprocess probe used by the lifecycle and contracts gates.

Run as ``python -I probe.py <spec.json>`` so the child process starts from a
clean interpreter (no PYTHONPATH, no user site). The probe:

- installs interception for network calls, process creation, thread starts,
  atexit registrations, logging handler changes, import-hook and signal
  handler mutation;
- snapshots process-wide state, imports the configured target module(s),
  and diffs the snapshots to detect application-visible side effects;
- exercises the manifest's activation API for idempotency and cleanup, and
  re-runs activation in fork/spawn children to prove single activation;
- emits a single JSON object on stdout (everything else goes to stderr).

The probe is deliberately dependency-free so it can run under any supported
interpreter, including inside minimal virtual environments.
"""

from __future__ import annotations

import atexit
from contextlib import redirect_stdout
import importlib
import io
import json
import logging
import multiprocessing
import os
import signal
import socket
import subprocess  # nosec B404
import sys
import threading
import traceback
from typing import Any
from typing import Callable


# Phases let the interceptor distinguish "import time" (nothing may happen)
# from "explicit activation" (threads are allowed, but must stop on
# deactivate and must not duplicate).
PHASE = "setup"
VIOLATIONS: list[dict[str, Any]] = []


def _violation(kind: str, detail: str) -> None:
    VIOLATIONS.append({"phase": PHASE, "kind": kind, "detail": detail})


def _record_and_raise(kind: str, detail: str) -> Callable[..., Any]:
    def guard(*args: Any, **kwargs: Any) -> Any:
        _violation(kind, detail)
        raise RuntimeError("package-split probe blocked %s" % kind)

    return guard


def _install_interceptors() -> None:
    # Network and process creation are recorded; they abort the probe in
    # import/contracts phases but call through during the explicit
    # activation phases (activation may legitimately open sockets or spawn
    # workers -- the reporting layer exempts activation-phase network and
    # process effects, and blocking them would abort the probe before
    # idempotency and cleanup can be evaluated).
    activation_phases = ("activation", "second-activation", "deactivation")

    def _record_or_passthrough(kind: str, detail: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def guard(*args: Any, **kwargs: Any) -> Any:
            if PHASE in activation_phases:
                return real(*args, **kwargs)
            _violation(kind, detail % PHASE)
            raise RuntimeError("package-split probe blocked %s" % kind)

        return guard

    real_socket = socket.socket

    class ProbedSocket(socket.socket):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if PHASE in activation_phases:
                real_socket.__init__(self, *args, **kwargs)  # noqa: SLF001
                return
            _violation("network", "socket.socket() during %s" % PHASE)
            raise RuntimeError("package-split probe blocked socket creation")

    socket.socket = ProbedSocket  # type: ignore[misc] # nosec B603
    socket.create_connection = _record_or_passthrough(
        "network", "socket.create_connection during %s", socket.create_connection
    )
    socket.getaddrinfo = _record_or_passthrough("network", "socket.getaddrinfo during %s", socket.getaddrinfo)

    # Process creation.
    subprocess.Popen = _record_or_passthrough("process", "subprocess.Popen during %s", subprocess.Popen)  # type: ignore[assignment, misc] # nosec B603
    os.system = _record_or_passthrough("process", "os.system during %s", os.system)  # nosec B603 # noqa: A005
    if hasattr(os, "posix_spawn"):
        os.posix_spawn = _record_or_passthrough("process", "os.posix_spawn during %s", os.posix_spawn)  # nosec B603

    # Thread starts are recorded, not blocked: activation may legitimately
    # start threads, imports may not.
    real_thread_start = threading.Thread.start

    def probed_start(self: threading.Thread) -> Any:
        _violation("thread-start", "threading.Thread.start during %s" % PHASE)
        return real_thread_start(self)

    threading.Thread.start = probed_start  # type: ignore[method-assign]

    real_atexit_register = atexit.register

    def probed_register(*args: Any, **kwargs: Any) -> Any:
        # multiprocessing registers its own exit handler when the first
        # Process is created; that is probe infrastructure, not a package
        # side effect, so it is exempted.
        callback = args[0] if args else None
        owner = getattr(callback, "__module__", "") or ""
        if owner.startswith("multiprocessing"):
            return real_atexit_register(*args, **kwargs)
        _violation("atexit", "atexit.register during %s" % PHASE)
        return real_atexit_register(*args, **kwargs)

    atexit.register = probed_register


def _threads_snapshot() -> list[tuple[str, int | None, bool, bool]]:
    return [(t.name, t.ident, t.daemon, t.is_alive()) for t in threading.enumerate()]


def _loggers_snapshot() -> dict[str, dict[str, Any]]:
    """Per-logger configuration: handlers, level, propagation, disabled."""
    snapshot: dict[str, dict[str, Any]] = {}

    def record(name: str, logger: logging.Logger) -> None:
        snapshot[name] = {
            "handlers": [id(h) for h in logger.handlers],
            "level": logger.level,
            "propagate": logger.propagate,
            "disabled": logger.disabled,
        }

    record("<root>", logging.getLogger())
    for name in list(logging.Logger.manager.loggerDict):
        logger = logging.getLogger(name)
        if isinstance(logger, logging.Logger):
            record(name, logger)
    return snapshot


def _loggers_delta(before: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Configuration changes between two snapshots, any direction.

    Import inertness requires that imports neither add nor remove handlers
    nor change logging configuration (levels, propagation, disabled state).
    """
    after = _loggers_snapshot()
    delta: dict[str, dict[str, Any]] = {}
    for name, current in after.items():
        previous = before.get(name)
        if previous is None:
            delta[name] = {"change": "logger-created", "config": current}
            continue
        changes: dict[str, Any] = {}
        added = [h for h in current["handlers"] if h not in previous["handlers"]]
        removed = [h for h in previous["handlers"] if h not in current["handlers"]]
        if added:
            changes["handlers_added"] = len(added)
        if removed:
            changes["handlers_removed"] = len(removed)
        for key in ("level", "propagate", "disabled"):
            if previous[key] != current[key]:
                changes[key] = (previous[key], current[key])
        if changes:
            delta[name] = changes
    return delta


def _signals_snapshot() -> dict[int, int]:
    snapshot: dict[int, int] = {}
    for signum in range(1, signal.NSIG):
        try:
            handler = signal.getsignal(signum)
        except (ValueError, OSError, RuntimeError):
            continue
        snapshot[signum] = id(handler)
    return snapshot


def _meta_snapshot() -> dict[str, Any]:
    return {
        "meta_path": [id(f) for f in sys.meta_path],
        "path_hooks": [id(f) for f in sys.path_hooks],
        "atexit_callbacks": atexit._ncallbacks(),
    }


def _meta_delta(before: dict[str, Any]) -> dict[str, int]:
    after = _meta_snapshot()
    return {
        "meta_path": int(after["meta_path"] != before["meta_path"]),
        "path_hooks": int(after["path_hooks"] != before["path_hooks"]),
        "atexit_callbacks": after["atexit_callbacks"] - before["atexit_callbacks"],
    }


def _import_side_effects(spec: dict[str, Any]) -> dict[str, Any]:
    """Import each target module and diff process-wide state around it."""
    results: dict[str, Any] = {}
    global PHASE
    PHASE = "import"

    # Grab references to protected callables before importing the targets.
    protected: dict[str, Any] = {}
    for reference in spec.get("protected_callables", []):
        module_name, _, attr = reference.partition(":")
        try:
            holder = importlib.import_module(module_name)
            protected[reference] = getattr(holder, attr)
        except Exception as exc:
            results.setdefault("protected_callable_errors", {})[reference] = repr(exc)

    before_threads = _threads_snapshot()
    before_loggers = _loggers_snapshot()
    before_signals = _signals_snapshot()
    before_meta = _meta_snapshot()

    for target in spec.get("import_targets", []):
        entry: dict[str, Any] = {}
        try:
            importlib.import_module(target)
            entry["ok"] = True
        except Exception as exc:
            entry.update(ok=False, error=repr(exc), traceback=traceback.format_exc())
        results.setdefault("imports", {})[target] = entry

    after_threads = _threads_snapshot()
    new_threads = [t for t in after_threads if t not in before_threads]
    if new_threads:
        _violation("import-thread", "new threads alive after import: %r" % (new_threads,))
    logger_delta = _loggers_delta(before_loggers)
    if logger_delta:
        _violation("logging", "logging configuration changed during import: %r" % (logger_delta,))
    after_signals = _signals_snapshot()
    changed = [s for s, h in after_signals.items() if before_signals.get(s) != h]
    if changed:
        _violation("signal", "signal handlers changed during import: %r" % (changed,))
    meta_delta = _meta_delta(before_meta)
    if any(meta_delta.values()):
        _violation("import-hooks", "import machinery mutated during import: %r" % (meta_delta,))

    # Patched callables: identity must be unchanged by the target imports.
    for reference, original in protected.items():
        module_name, _, attr = reference.partition(":")
        try:
            holder = importlib.import_module(module_name)
            current = getattr(holder, attr)
        except Exception as exc:
            results.setdefault("protected_callable_errors", {})[reference] = repr(exc)
            continue
        if current is not original:
            _violation("patched-callable", "%s was replaced during import" % reference)

    PHASE = "setup"
    return results


def _singleton_lengths(singletons: list[str]) -> dict[str, int]:
    lengths: dict[str, int] = {}
    for reference in singletons:
        mod_name, _, attr = reference.partition(":")
        owner = importlib.import_module(mod_name)
        value = getattr(owner, attr)
        lengths[reference] = len(value) if hasattr(value, "__len__") else 1
    return lengths


def _activation(spec: dict[str, Any]) -> dict[str, Any]:
    """Exercise activate/deactivate: idempotency, cleanup, fork/spawn."""
    global PHASE
    api: dict[str, Any] = spec.get("activation") or {}
    if not api:
        return {}
    results: dict[str, Any] = {}

    module = importlib.import_module(api["module"])
    singletons: list[str] = api.get("singletons", [])
    activate: Callable[[], Any] = getattr(module, api["activate"])
    # A declared entry point that does not resolve is a contract error, not
    # a missing optional hook: getattr's default would silently skip
    # cleanup checks for a manifest naming a nonexistent deactivation.
    deactivate: Callable[[], Any] | None = getattr(module, api["deactivate"]) if "deactivate" in api else None

    baseline_threads = _threads_snapshot()
    baseline_singletons = _singleton_lengths(singletons)
    PHASE = "activation"
    activate()
    after_first: dict[str, Any] = {
        "threads": [t for t in _threads_snapshot()],
        "singletons": _singleton_lengths(singletons),
    }
    PHASE = "second-activation"
    activate()
    after_second: dict[str, Any] = {
        "threads": [t for t in _threads_snapshot()],
        "singletons": _singleton_lengths(singletons),
    }
    PHASE = "setup"

    if after_second["singletons"] != after_first["singletons"]:
        _violation(
            "duplicate-activation",
            "registry changed on second activate(): %r -> %r" % (after_first["singletons"], after_second["singletons"]),
        )

    # Idempotent activation may not leak workers per call: any live thread
    # after the second activate() that was not present after the first is
    # an additional worker spawned by the repeated call.
    first_thread_ids = {t[1] for t in after_first["threads"]}
    extra_threads = [t for t in after_second["threads"] if t[1] not in first_thread_ids]
    if extra_threads:
        _violation(
            "duplicate-activation",
            "second activate() started additional live threads: %r" % (extra_threads,),
        )

    if deactivate is not None:
        PHASE = "deactivation"
        deactivate()
        PHASE = "setup"
        # Threads started by activation must stop; allow a short grace
        # period because writers typically poll on an interval. Anything
        # that was alive before activate() (interpreter machinery) is fine.
        import time

        if _singleton_lengths(singletons) != baseline_singletons:
            _violation("activation-cleanup", "registries not restored after deactivate()")
        started_by_activation = [t for t in after_second["threads"] if t not in baseline_threads]
        deadline = time.time() + 5.0
        leftover: list[Any] = []
        while time.time() < deadline:
            alive = {t[1] for t in _threads_snapshot() if t[3]}
            leftover = [t for t in started_by_activation if t[1] in alive]
            if not leftover:
                break
            time.sleep(0.1)
        else:
            _violation("activation-cleanup", "threads still alive after deactivate(): %r" % (leftover,))

    # fork/spawn children: activation must work exactly once per process.
    for mode in spec.get("multiprocessing_modes", []):
        try:
            ctx = multiprocessing.get_context(mode)
            queue: Any = ctx.Queue()
            process = ctx.Process(
                target=_child_activation,
                args=(api, singletons, queue),
            )
            process.start()
            process.join(timeout=60)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            child: Any = None
            if process.exitcode == 0:
                try:
                    child = queue.get(timeout=10)
                except Exception:
                    child = None
            results[mode] = {
                "exitcode": process.exitcode,
                "child": child,
            }
            if process.exitcode != 0:
                _violation("mp-%s" % mode, "child exited with %r" % process.exitcode)
            elif child and not child.get("ok"):
                _violation("mp-%s" % mode, "child activation failed: %r" % (child,))
            elif child and any(v != 1 for v in child.get("singletons", {}).values()):
                _violation(
                    "mp-%s" % mode,
                    "child registered more than one instance: %r" % (child["singletons"],),
                )
        except Exception as exc:
            _violation("mp-%s" % mode, repr(exc))
    return results


def _child_activation(api: dict[str, Any], singletons: list[str], queue: Any) -> None:
    # Child processes run this function directly (no top-level probe code).
    try:
        module = importlib.import_module(api["module"])
        activate: Callable[[], Any] = getattr(module, api["activate"])
        deactivate: Callable[[], Any] | None = getattr(module, api["deactivate"]) if "deactivate" in api else None
        try:
            activate()
            lengths: dict[str, int] = {}
            for reference in singletons:
                mod_name, _, attr = reference.partition(":")
                owner = importlib.import_module(mod_name)
                value = getattr(owner, attr)
                lengths[reference] = len(value) if hasattr(value, "__len__") else 1
            queue.put({"ok": True, "singletons": lengths})
        finally:
            # Deactivation must run before the child exits: a valid
            # activation can start non-daemon workers and stop them during
            # deactivation, and the child would otherwise hang on exit
            # waiting for them.
            if deactivate is not None:
                deactivate()
    except Exception:
        queue.put({"ok": False, "error": traceback.format_exc()})
        sys.exit(1)


def _contracts_snapshot(spec: dict[str, Any]) -> dict[str, Any]:
    """Introspect supported exports for the contracts gate."""
    global PHASE
    PHASE = "contracts"
    output: dict[str, Any] = {}
    for module_name, exports in spec.get("supported_exports", {}).items():
        entry: dict[str, Any] = output.setdefault(module_name, {})
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            entry["<module>"] = {"error": repr(exc)}
            continue
        for export in exports:
            try:
                value = getattr(module, export)
            except AttributeError:
                entry[export] = {"missing": True}
                continue
            entry[export] = _describe(value)
    PHASE = "setup"
    return output


def _describe(value: Any) -> dict[str, Any]:
    import inspect

    described: dict[str, Any] = {"kind": type(value).__name__}
    if callable(value):
        try:
            signature = inspect.signature(value)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            described["signature"] = str(signature)
            described["defaults"] = {
                name: repr(parameter.default)
                for name, parameter in signature.parameters.items()
                if parameter.default is not inspect.Parameter.empty
            }
            described["annotations"] = {
                name: _safe_repr(parameter.annotation) for name, parameter in signature.parameters.items()
            }
    if inspect.isclass(value):
        described["bases"] = [base.__name__ for base in value.__bases__]
        described["metaclass"] = type(value).__name__
    described["module"] = getattr(value, "__module__", None)
    return described


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return "<unreprable>"


def _identity_probe(spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve bridge attributes and report their origin (migration gate)."""
    global PHASE
    PHASE = "identity"
    output: dict[str, Any] = {}
    for reference in spec.get("identity", []):
        module_name, _, attr = reference.partition(":")
        entry: dict[str, Any] = {}
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, attr)
            entry["ok"] = True
            entry["origin_module"] = getattr(value, "__module__", None)
            entry["class_name"] = getattr(value, "__name__", None)
        except Exception as exc:
            entry.update(ok=False, error=repr(exc))
        output[reference] = entry
    for module_name in spec.get("imports_ok", []):
        try:
            module = importlib.import_module(module_name)
            output.setdefault("imports_ok", {})[module_name] = {
                "ok": True,
                "file": getattr(module, "__file__", None),
            }
        except Exception as exc:
            output.setdefault("imports_ok", {})[module_name] = {"ok": False, "error": repr(exc)}
    for module_name in spec.get("imports_fail", []):
        try:
            importlib.import_module(module_name)
            output.setdefault("imports_fail", {})[module_name] = {
                "ok": True,
                "error": "import unexpectedly succeeded",
            }
        except Exception as exc:
            output.setdefault("imports_fail", {})[module_name] = {"ok": False, "error": repr(exc)}
    ep = spec.get("entry_point_counts")
    if ep:
        from importlib.metadata import entry_points

        counts: dict[str, int] = {}
        for group in ep:
            counts[group] = len(list(entry_points(group=group)))
        output["entry_point_counts"] = counts
    PHASE = "setup"
    return output


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: probe.py <spec.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        spec = json.load(f)

    # sys.path entries requested by the caller (e.g. an unpacked wheel).
    for entry in spec.get("sys_path", []):
        sys.path.insert(0, entry)

    result: dict[str, Any] = {
        "ok": None,
        "spec": spec,
        "import_side_effects": {},
        "activation": {},
        "contracts": {},
        "identity": {},
    }
    try:
        with redirect_stdout(sys.stderr):
            _install_interceptors()
            result["import_side_effects"] = _import_side_effects(spec)
            if spec.get("activation"):
                result["activation"] = _activation(spec)
            if spec.get("supported_exports"):
                result["contracts"] = _contracts_snapshot(spec)
            if (
                spec.get("identity")
                or spec.get("imports_ok")
                or spec.get("imports_fail")
                or spec.get("entry_point_counts")
            ):
                result["identity"] = _identity_probe(spec)
            result["violations"] = list(VIOLATIONS)
            result["ok"] = not VIOLATIONS
    except Exception:
        result["ok"] = False
        result["error"] = traceback.format_exc()
    # Write to a scratch buffer so stdout contains exactly one JSON document.
    buffer = io.StringIO()
    json.dump(result, buffer, indent=2, default=_safe_repr)
    sys.stdout.write(buffer.getvalue())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
