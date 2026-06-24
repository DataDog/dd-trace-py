#include <Python.h>
#include <frameobject.h>

#include "gc_monitor.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <ctime>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace Datadog {

namespace {

using Clock = std::chrono::steady_clock;

inline size_t
elapsed_us(Clock::time_point start, Clock::time_point end)
{
    return static_cast<size_t>(std::chrono::duration_cast<std::chrono::microseconds>(end - start).count());
}

// Safely extract a UTF-8 string from a Python str/bytes object.
// Returns an empty string on failure.
std::string
pystr_to_std(PyObject* obj)
{
    if (obj == nullptr) {
        return {};
    }
    if (PyUnicode_Check(obj)) {
        const char* s = PyUnicode_AsUTF8(obj);
        return (s != nullptr) ? std::string(s) : std::string{};
    }
    if (PyBytes_Check(obj)) {
        const char* s = PyBytes_AS_STRING(obj);
        Py_ssize_t n = PyBytes_GET_SIZE(obj);
        return std::string(s, static_cast<size_t>(n));
    }
    return {};
}

// Return the fully qualified type name "module.qualname".
// GIL must be held.
[[maybe_unused]] std::string
type_name_of(PyObject* obj)
{
    PyObject* tp = reinterpret_cast<PyObject*>(Py_TYPE(obj));

    PyObject* mod = PyObject_GetAttrString(tp, "__module__");
    PyObject* qname = PyObject_GetAttrString(tp, "__qualname__");

    std::string mod_s = pystr_to_std(mod);
    std::string qname_s = pystr_to_std(qname);

    Py_XDECREF(mod);
    Py_XDECREF(qname);
    PyErr_Clear();

    if (mod_s.empty() || mod_s == "builtins") {
        return qname_s.empty() ? "<unknown>" : qname_s;
    }
    if (qname_s.empty()) {
        return mod_s;
    }
    return mod_s + "." + qname_s;
}

// Escape a string for JSON output (handles \, ", and control chars).
std::string
json_escape(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 4);
    for (unsigned char c : s) {
        if (c == '"') {
            out += "\\\"";
        } else if (c == '\\') {
            out += "\\\\";
        } else if (c == '\n') {
            out += "\\n";
        } else if (c == '\r') {
            out += "\\r";
        } else if (c == '\t') {
            out += "\\t";
        } else if (c < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned>(c));
            out += buf;
        } else {
            out += static_cast<char>(c);
        }
    }
    return out;
}

// Serialize a single TreeNode (non-root) recursively into the stream.
void
serialize_node(std::ostringstream& out, const TreeNode& node, int indent)
{
    std::string pad(static_cast<size_t>(indent * 2), ' ');
    out << pad << "{\"t\":" << node.type_idx << ",\"ic\":" << node.ic << ",\"ts\":" << node.ts;
    if (!node.children.empty()) {
        out << ",\"ch\":[";
        for (size_t i = 0; i < node.children.size(); ++i) {
            if (i > 0) {
                out << ",";
            }
            serialize_node(out, node.children[i], indent + 1);
        }
        out << "]";
    }
    out << "}";
}

// Returns true when the type name matches a pattern that is known to produce
// noisy / unactionable leak candidates.  Objects of these types are still
// tracked for survivor-count purposes but are never promoted to the suspect
// list that gets serialised.
[[maybe_unused]] bool
is_excluded_type(const std::string& tname) noexcept
{
    // Exact matches --------------------------------------------------------
    // Python descriptor/slot types and other infrastructure objects that are
    // always long-lived by design.
    static const std::unordered_set<std::string> exact = {
        // Descriptor / slot types -- always long-lived by design
        "method",
        "property",
        "wrapper_descriptor",
        "method_descriptor",
        "staticmethod",
        "classmethod_descriptor",
        "getset_descriptor",
        "member_descriptor",
        // Persistent mapping types (pyrsistent / immutable hash-array maps)
        "hamt",
        "hamt_bitmap_node",
        "hamt_array_node",
        // C-level buffer wrappers
        "managedbuffer",
        "memoryview",
        // Generic built-in containers: always category "O" (C-ext held) in
        // practice; any real leak surfaces through a more specific application
        // type anyway, and these only push actionable suspects off the top-N.
        "dict",
        "list",
        "set",
        "frozenset",
        "tuple",
    };
    if (exact.count(tname) != 0) {
        return true;
    }

    // Prefix matches -------------------------------------------------------
    // Each entry is matched as a prefix of tname so that e.g. "cassandra."
    // catches "cassandra.cluster.Cluster", "cassandra.pool.Host", etc.
    static const std::string prefixes[] = {
        "cassandra.", // Cassandra driver internals (C extension + Python)
        "ddtrace.",   // tracer / profiler own objects
        "_thread.",   // low-level threading primitives
        "weakref.",   // weak reference types
        "_frozen",    // _frozen_importlib* bootstrap modules
        "_sitebuiltins",
        "importlib.", // importlib.metadata, importlib_metadata, etc.
        "importlib_", // importlib_metadata backport
        "logging.",      "signal.", "typing.", "ast.", "bytecode.",
    };
    for (const auto& p : prefixes) {
        if (tname.size() >= p.size() && tname.compare(0, p.size(), p) == 0) {
            return true;
        }
    }
    return false;
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// GCMonitor singleton
// ---------------------------------------------------------------------------

GCMonitor&
GCMonitor::get()
{
    static GCMonitor instance;
    return instance;
}

void
GCMonitor::start(uint64_t interval_ms, int survivor_threshold, int top_n, bool referrers_enabled)
{
    std::unique_lock<std::mutex> lock(_mutex);
    if (_started) {
        return;
    }
    _interval_ms = interval_ms;
    _survivor_threshold = survivor_threshold;
    _top_n = top_n;
    _referrers_enabled = referrers_enabled;
    _started = true;
    _stop_flag = false;
    lock.unlock();

    _thread = std::thread(&GCMonitor::thread_main, this);
    _thread.detach(); // we never join; shutdown is signal-only
}

void
GCMonitor::stop()
{
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _stop_flag = true;
        _started = false;
    }
    _cv.notify_one();
    // Do not join -- caller must not block on shutdown.
}

std::string
GCMonitor::get_latest_json() const
{
    std::lock_guard<std::mutex> lock(_mutex);
    return _latest_json;
}

void
GCMonitor::thread_main()
{
    while (true) {
        std::unique_lock<std::mutex> lock(_mutex);
        _cv.wait_for(lock, std::chrono::milliseconds(_interval_ms));
        if (_stop_flag) {
            return;
        }
        lock.unlock();

        // Run the snapshot with the GIL
        take_snapshot();
    }
}

void
GCMonitor::take_snapshot()
{
    ProfilerStats::GCSnapshotTiming timing{};
    const auto t_wall_start = Clock::now();

    // ------------------------------------------------------------------
    // Phase 1 (GIL held): GC engine stats + get_objects
    //
    // We keep this section as short as possible.  The only reason we need
    // the GIL here is to call Python API functions.
    // ------------------------------------------------------------------
    PyGILState_STATE gstate = PyGILState_Ensure();
    const auto t_gc_stats_start = Clock::now();

    PyObject* gc_mod = PyImport_ImportModule("gc");
    if (gc_mod == nullptr) {
        PyErr_Clear();
        PyGILState_Release(gstate);
        return;
    }

    bool gc_enabled = false;
    {
        PyObject* res = PyObject_CallMethod(gc_mod, "isenabled", nullptr);
        if (res != nullptr) {
            gc_enabled = PyObject_IsTrue(res) != 0;
            Py_DECREF(res);
        }
        PyErr_Clear();
    }

    std::array<int, 3> thresholds{ 0, 0, 0 };
    {
        PyObject* res = PyObject_CallMethod(gc_mod, "get_threshold", nullptr);
        if (res != nullptr && PyTuple_Check(res) && PyTuple_GET_SIZE(res) >= 3) {
            for (int i = 0; i < 3; ++i) {
                PyObject* v = PyTuple_GET_ITEM(res, i);
                thresholds[i] = PyLong_Check(v) ? static_cast<int>(PyLong_AsLong(v)) : 0;
            }
        }
        Py_XDECREF(res);
        PyErr_Clear();
    }

    int garbage_count = 0;
    {
        PyObject* garbage = PyObject_GetAttrString(gc_mod, "garbage");
        if (garbage != nullptr) {
            garbage_count = static_cast<int>(PyList_Check(garbage) ? PyList_GET_SIZE(garbage) : 0);
            Py_DECREF(garbage);
        }
        PyErr_Clear();
    }

    std::array<GCGenStats, 3> gen_stats{};
    {
        PyObject* res = PyObject_CallMethod(gc_mod, "get_stats", nullptr);
        if (res != nullptr && PyList_Check(res) && PyList_GET_SIZE(res) >= 3) {
            for (int i = 0; i < 3; ++i) {
                PyObject* d = PyList_GET_ITEM(res, i);
                if (PyDict_Check(d)) {
                    auto get_u64 = [&](const char* key) -> uint64_t {
                        PyObject* v = PyDict_GetItemString(d, key);
                        if (v != nullptr && PyLong_Check(v)) {
                            return static_cast<uint64_t>(PyLong_AsUnsignedLongLong(v));
                        }
                        return 0;
                    };
                    gen_stats[i].n = get_u64("collections");
                    gen_stats[i].col = get_u64("collected");
                    gen_stats[i].uncol = get_u64("uncollectable");
                }
            }
        }
        Py_XDECREF(res);
        PyErr_Clear();
    }

    std::array<GCGenStats, 3> delta_stats{};
    for (int i = 0; i < 3; ++i) {
        delta_stats[i].n =
          gen_stats[i].n >= _prev_gen_stats[i].n ? gen_stats[i].n - _prev_gen_stats[i].n : gen_stats[i].n;
        delta_stats[i].col =
          gen_stats[i].col >= _prev_gen_stats[i].col ? gen_stats[i].col - _prev_gen_stats[i].col : gen_stats[i].col;
        delta_stats[i].uncol = gen_stats[i].uncol >= _prev_gen_stats[i].uncol
                                 ? gen_stats[i].uncol - _prev_gen_stats[i].uncol
                                 : gen_stats[i].uncol;
    }
    _prev_gen_stats = gen_stats;

    const auto t_get_objects_start = Clock::now();
    timing.gc_stats_us = elapsed_us(t_gc_stats_start, t_get_objects_start);

    PyObject* objs = PyObject_CallMethod(gc_mod, "get_objects", nullptr);
    Py_DECREF(gc_mod);

    if (objs == nullptr || !PyList_Check(objs)) {
        Py_XDECREF(objs);
        PyErr_Clear();
        PyGILState_Release(gstate);
        return;
    }

    Py_ssize_t n_objs = PyList_GET_SIZE(objs);

    // Copy raw pointers into a C++ vector.  PyList_GET_ITEM returns borrowed
    // references; the pointers remain valid as long as `objs` is alive.
    std::vector<PyObject*> ptrs(static_cast<size_t>(n_objs));
    for (Py_ssize_t i = 0; i < n_objs; ++i) {
        ptrs[static_cast<size_t>(i)] = PyList_GET_ITEM(objs, i);
    }

    // Release the GIL.  `objs` owns a reference to every object in `ptrs`,
    // so no pointer in `ptrs` can be freed until we Py_DECREF(objs) below.
    // Direct dereference of ob_type is therefore safe without the GIL.
    const auto t_type_scan_start = Clock::now();
    timing.get_objects_us = elapsed_us(t_get_objects_start, t_type_scan_start);
    PyGILState_Release(gstate);

    // ------------------------------------------------------------------
    // Phase 2 (GIL released): build type histogram
    //
    // We read ob_type for each object and tally counts by PyTypeObject*.
    // No Python API calls, no refcount operations -- just a struct-field
    // read and a hashmap update.  Python threads are free to run while we
    // do the O(n) work here.
    // ------------------------------------------------------------------
    std::unordered_map<PyTypeObject*, uint32_t> type_hist;
    type_hist.reserve(static_cast<size_t>(n_objs / 8));

    for (PyObject* obj : ptrs) {
        type_hist[Py_TYPE(obj)]++;
    }

    // ------------------------------------------------------------------
    // Phase 3 (GIL re-acquired): resolve type names, release object list
    //
    // We call type_name_of once per *unique type*, not once per instance.
    // In a typical process there are O(hundreds) of unique types but
    // O(millions) of instances, so this is orders of magnitude cheaper
    // than the previous per-instance approach.
    //
    // We resolve names while `objs` is still alive to guarantee that the
    // PyTypeObject* keys in type_hist are valid (a heap type's refcount
    // includes a contribution from each of its live instances).
    // ------------------------------------------------------------------
    gstate = PyGILState_Ensure();
    const auto t_name_resolve_start = Clock::now();
    timing.type_scan_us = elapsed_us(t_type_scan_start, t_name_resolve_start);

    std::vector<std::string> type_table;
    std::unordered_map<std::string, uint32_t> type_table_index;
    std::vector<uint32_t> type_counts;
    type_table.reserve(type_hist.size());
    type_counts.reserve(type_hist.size());

    for (auto& [tp, count] : type_hist) {
        std::string tname;
        if (tp != nullptr) {
            // Reuse type_name_of by passing a dummy instance: we only need
            // __module__ and __qualname__ from the type, so pass the type
            // object itself as the argument (its own type is `type`, but we
            // call GetAttrString on tp directly).
            PyObject* tp_obj = reinterpret_cast<PyObject*>(tp);
            PyObject* mod = PyObject_GetAttrString(tp_obj, "__module__");
            PyObject* qname = PyObject_GetAttrString(tp_obj, "__qualname__");
            std::string mod_s = pystr_to_std(mod);
            std::string qname_s = pystr_to_std(qname);
            Py_XDECREF(mod);
            Py_XDECREF(qname);
            PyErr_Clear();
            if (mod_s.empty() || mod_s == "builtins") {
                tname = qname_s.empty() ? "<unknown>" : qname_s;
            } else if (qname_s.empty()) {
                tname = mod_s;
            } else {
                tname = mod_s + "." + qname_s;
            }
        } else {
            tname = "<unknown>";
        }

        auto it = type_table_index.find(tname);
        if (it == type_table_index.end()) {
            auto tidx = static_cast<uint32_t>(type_table.size());
            type_table.push_back(tname);
            type_table_index[tname] = tidx;
            type_counts.push_back(count);
        } else {
            type_counts[it->second] += count;
        }
    }

    // Release the object list now that we no longer need the type pointers.
    Py_DECREF(objs);
    PyErr_Clear();
    PyGILState_Release(gstate);

    // ------------------------------------------------------------------
    // Phase 4 (GIL released): serialize
    // ------------------------------------------------------------------
    const auto t_serialize_start = Clock::now();
    timing.name_resolve_us = elapsed_us(t_name_resolve_start, t_serialize_start);

    std::vector<RootNode> roots;
    serialize(gen_stats, delta_stats, gc_enabled, thresholds, garbage_count, type_table, type_counts, roots);

    const auto t_wall_end = Clock::now();
    timing.serialize_us = elapsed_us(t_serialize_start, t_wall_end);
    timing.wall_us = elapsed_us(t_wall_start, t_wall_end);

    if (ProfilerState::get().is_initialized()) {
        auto borrow = ProfilerState::get().profile_state.borrow();
        borrow.stats().add_gc_snapshot_timing(timing);
    }
}

void
GCMonitor::serialize(const std::array<GCGenStats, 3>& gen_stats,
                     const std::array<GCGenStats, 3>& delta_stats,
                     bool gc_enabled,
                     const std::array<int, 3>& thresholds,
                     int garbage_count,
                     const std::vector<std::string>& type_table,
                     const std::vector<uint32_t>& type_counts,
                     const std::vector<RootNode>& roots)
{
    // Compute snapshot time (ns since epoch)
    auto now = std::chrono::system_clock::now();
    auto ts_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count());

    std::ostringstream out;
    out << "{\"v\":1"
        << ",\"ts_ns\":" << ts_ns;

    // gc block
    out << ",\"gc\":{"
        << "\"enabled\":" << (gc_enabled ? "true" : "false") << ",\"thresholds\":[" << thresholds[0] << ","
        << thresholds[1] << "," << thresholds[2] << "]"
        << ",\"garbage\":" << garbage_count << ",\"gen\":[";
    for (int i = 0; i < 3; ++i) {
        if (i > 0) {
            out << ",";
        }
        out << "{\"n\":" << gen_stats[i].n << ",\"col\":" << gen_stats[i].col << ",\"uncol\":" << gen_stats[i].uncol
            << "}";
    }
    out << "],\"d_gen\":[";
    for (int i = 0; i < 3; ++i) {
        if (i > 0) {
            out << ",";
        }
        out << "{\"n\":" << delta_stats[i].n << ",\"col\":" << delta_stats[i].col
            << ",\"uncol\":" << delta_stats[i].uncol << "}";
    }
    out << "]}";

    // type table
    out << ",\"tt\":[";
    for (size_t i = 0; i < type_table.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        out << "\"" << json_escape(type_table[i]) << "\"";
    }
    out << "]";

    // per-type instance counts (parallel array to "tt")
    out << ",\"tc\":[";
    for (size_t i = 0; i < type_counts.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        out << type_counts[i];
    }
    out << "]";

    // reference tree roots
    out << ",\"r\":[";
    for (size_t i = 0; i < roots.size(); ++i) {
        const RootNode& r = roots[i];
        if (i > 0) {
            out << ",";
        }
        out << "{\"t\":" << r.type_idx << ",\"c\":\"" << r.category << "\""
            << ",\"ic\":" << r.ic << ",\"ts\":" << r.ts;
        if (!r.fn.empty()) {
            out << ",\"fn\":\"" << json_escape(r.fn) << "\"";
        }
        if (!r.children.empty()) {
            out << ",\"ch\":[";
            for (size_t j = 0; j < r.children.size(); ++j) {
                if (j > 0) {
                    out << ",";
                }
                serialize_node(out, r.children[j], 0);
            }
            out << "]";
        }
        out << "}";
    }
    out << "]}";

    std::lock_guard<std::mutex> lock(_mutex);
    _latest_json = out.str();
}

} // namespace Datadog
