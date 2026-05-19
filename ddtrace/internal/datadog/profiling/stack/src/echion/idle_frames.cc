#include <echion/idle_frames.h>

#include <echion/echion_sampler.h>
#include <echion/strings.h>

#include "dd_wrapper/include/profiler_state.hpp"

#include <array>
#include <string_view>

namespace {

// A Python frame is considered "idle" if its co_name matches `name` (matching
// CPython's qualified-name behaviour on 3.11+ when `name` contains a dot) AND
// the basename of co_filename equals `filename`.
struct IdleFramePattern
{
    std::string_view name;
    std::string_view filename;
};

// Conservative allowlist. Only entries whose body is essentially `kernel
// wait` should appear here — see idle_frames.h. Names use qualified form on
// Python 3.11+ (the form CPython stores in co_qualname). On older versions
// the unqualified form ("wait", "join", ...) is the one stored in co_name;
// we cover that by matching both the qualified and unqualified suffix below.
constexpr std::array<IdleFramePattern, 27> PYTHON_IDLE_PATTERNS = { {
  // threading: blocking synchronization primitives
  { "Event.wait", "threading.py" },
  { "Condition.wait", "threading.py" },
  { "Condition.wait_for", "threading.py" },
  { "Barrier.wait", "threading.py" },
  { "Thread.join", "threading.py" },
  { "Thread._wait_for_tstate_lock", "threading.py" },
  { "Semaphore.acquire", "threading.py" },
  { "BoundedSemaphore.acquire", "threading.py" },

  // queue: backed by Condition.wait
  { "Queue.get", "queue.py" },
  { "Queue.put", "queue.py" },
  { "SimpleQueue.get", "queue.py" },

  // selectors: blocking I/O multiplexing
  { "BaseSelector.select", "selectors.py" },
  { "SelectSelector.select", "selectors.py" },
  { "PollSelector.select", "selectors.py" },
  { "EpollSelector.select", "selectors.py" },
  { "KqueueSelector.select", "selectors.py" },
  { "DevpollSelector.select", "selectors.py" },
  { "_PollLikeSelector.select", "selectors.py" },

  // concurrent.futures: waiting on results
  { "Future.result", "_base.py" },
  { "Future.exception", "_base.py" },
  { "wait", "_base.py" },
  { "as_completed", "_base.py" },

  // subprocess: process synchronization
  { "Popen.wait", "subprocess.py" },
  { "Popen._wait", "subprocess.py" },

  // multiprocessing
  { "BaseProcess.join", "process.py" },
  { "Connection.recv", "connection.py" },
  { "Connection.recv_bytes", "connection.py" },
} };

// (module, name) pairs for C-level callables that are known to block. These
// are reported via sys.monitoring CALL events (Python 3.12+) and looked up
// through the native call registry. On older Pythons the registry is empty,
// so this code path is simply inert.
struct NativeIdleCall
{
    std::string_view module;
    std::string_view name;
};

constexpr std::array<NativeIdleCall, 11> NATIVE_IDLE_CALLS = { {
  { "time", "sleep" },
  { "select", "select" },
  { "select", "poll" },
  { "select", "epoll" },
  { "select", "kqueue" },
  { "select", "devpoll" },
  // _thread.lock is the underlying type for threading.Lock / RLock. Its
  // blocking acquire is reported by sys.monitoring as a method call on the
  // lock instance.
  { "_thread", "lock.acquire" },
  { "_thread", "RLock.acquire" },
  // os-level blocking reads/writes from selectors / asyncio internals. Most
  // callers use these from a selector, but a direct call also blocks.
  { "os", "wait" },
  { "os", "waitpid" },
  { "os", "waitid" },
} };

// Return the trailing basename of `path` (i.e. the substring after the last
// '/' or '\\'). If `path` has no separator, return `path` as-is. This is the
// portable equivalent of std::filesystem::path::filename without dragging in
// <filesystem>.
std::string_view
basename(std::string_view path)
{
    auto pos = path.find_last_of("/\\");
    if (pos == std::string_view::npos) {
        return path;
    }
    return path.substr(pos + 1);
}

bool
matches_python_pattern(std::string_view frame_name, std::string_view file_basename)
{
    for (const auto& pat : PYTHON_IDLE_PATTERNS) {
        if (file_basename != pat.filename) {
            continue;
        }
        // Match either the full qualified name ("Event.wait" on 3.11+) or the
        // unqualified suffix ("wait" on older Pythons; CPython stores the
        // unqualified name in co_name on 3.10-).
        if (frame_name == pat.name) {
            return true;
        }
        auto dot = pat.name.find('.');
        if (dot != std::string_view::npos && frame_name == pat.name.substr(dot + 1)) {
            return true;
        }
    }
    return false;
}

bool
matches_native_idle_call(const Datadog::NativeCallEntry& entry)
{
    for (const auto& call : NATIVE_IDLE_CALLS) {
        if (entry.module != call.module) {
            continue;
        }
        if (entry.name == call.name) {
            return true;
        }
        // sys.monitoring's __qualname__ for unbound methods sometimes carries
        // the owning type (e.g. "lock.acquire"); accept the bare method name
        // as a fallback (e.g. "acquire").
        auto dot = call.name.find('.');
        if (dot != std::string_view::npos && entry.name == call.name.substr(dot + 1)) {
            return true;
        }
    }
    return false;
}

} // namespace

bool
is_idle_python_frame(EchionSampler& echion, const Frame& frame)
{
    // First, consult sys.monitoring's native-call registry. When a Python
    // frame is paused inside a known-blocking C call (time.sleep,
    // select.select, ...) this captures the cases that the Python-level
    // allowlist alone would miss.
    if (frame.code_object != 0 && frame.lasti >= 0) {
        auto& registry = Datadog::ProfilerState::get().native_call_registry;
        int offset_bytes = frame.lasti * static_cast<int>(sizeof(_Py_CODEUNIT));
        if (auto maybe_entry = registry.lookup(frame.code_object, offset_bytes, frame.first_lineno)) {
            if (matches_native_idle_call(maybe_entry->get())) {
                return true;
            }
        }
    }

    // Fall back to the Python-level wrapper allowlist.
    auto maybe_name = echion.string_table().lookup(frame.name);
    if (!maybe_name) {
        return false;
    }
    auto maybe_filename = echion.string_table().lookup(frame.filename);
    if (!maybe_filename) {
        return false;
    }

    return matches_python_pattern(maybe_name->get(), basename(maybe_filename->get()));
}
