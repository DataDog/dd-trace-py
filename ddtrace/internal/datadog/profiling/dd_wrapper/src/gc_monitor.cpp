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
std::string
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

// Walk the referrer chain from `obj` (up to max_depth steps) to find a
// recognizable GC root (Frame -> K, Module -> S, non-GC referrer -> O).
//
// Returns a RootNode where:
//   category / fn   -- anchor type and location
//   type_idx        -- type of the object at the *root* end of the chain
//                      (the object directly held by the Frame/Module/C-ext);
//                      equals obj's type when obj is anchored directly
//   ic / ts         -- always 1 / obj_size (caller aggregates multiple suspects)
//
// out_path receives the chain of (type_idx, size) pairs from the root-holder
// down to the suspect (exclusive of the root-holder, inclusive of the suspect).
// It is empty when obj is directly held by the anchor.
//
// cur_objs supplies pre-computed (type_idx, size) for every GC-tracked object,
// avoiding redundant sys.getsizeof calls on intermediate nodes.
//
// snapshot_objs is the list returned by gc.get_objects().  It is still alive
// during the walk and references every GC-tracked object, so it would appear
// as a referrer for every suspect and be mistakenly classified as 'O' (not in
// gc_id_set because it was created after the gc.get_objects() call).  We skip
// it explicitly the same way we skip the get_referrers() result list.
//
// GIL must be held.
RootNode
find_root(PyObject* obj,
          uint32_t obj_type_idx,
          uint64_t obj_size,
          int max_depth,
          const std::unordered_set<uintptr_t>& gc_id_set,
          const std::unordered_map<uintptr_t, std::pair<uint32_t, uint64_t>>& cur_objs,
          std::vector<std::string>& type_table,
          std::unordered_map<std::string, uint32_t>& type_table_index,
          PyObject* snapshot_objs,
          std::vector<std::pair<uint32_t, uint64_t>>& out_path)
{
    // Look up (type_idx, size) for any GC-tracked object via cur_objs.
    // Falls back to computing the type name when the object is not in cur_objs
    // (e.g. allocated after gc.get_objects was called).
    auto get_info = [&](PyObject* o) -> std::pair<uint32_t, uint64_t> {
        const uintptr_t oid = reinterpret_cast<uintptr_t>(o);
        const auto it = cur_objs.find(oid);
        if (it != cur_objs.end()) {
            return it->second;
        }
        std::string tname = type_name_of(o);
        const auto tit = type_table_index.find(tname);
        if (tit != type_table_index.end()) {
            return { tit->second, 0 };
        }
        const auto idx = static_cast<uint32_t>(type_table.size());
        type_table.push_back(tname);
        type_table_index[tname] = idx;
        return { idx, 0 };
    };

    RootNode root;
    root.category = '?';
    root.ic = 1;
    // ts holds the suspect's size so that the caller can aggregate suspect sizes
    // across merged roots (useful for ranking by retained bytes).
    root.ts = obj_size;

    // chain is built upward: chain[0] = suspect, chain[i>0] = intermediate
    // nodes walked toward the anchor.  Reversed at the end so that chain[0]
    // is the object directly held by the anchor.
    std::vector<std::pair<uint32_t, uint64_t>> chain;
    chain.push_back({ obj_type_idx, obj_size });

    // prev_obj is the object whose referrers we want to look up at each step.
    // We keep a borrowed reference -- the object stays alive because it is in
    // the gc heap (the caller holds the objs list).
    PyObject* prev_obj = obj;

    // Visited set prevents cycling between intermediate nodes.
    std::unordered_set<uintptr_t> visited;
    visited.insert(reinterpret_cast<uintptr_t>(obj));

    for (int depth = 0; depth < max_depth; ++depth) {
        PyObject* referrers =
          PyObject_CallMethod(reinterpret_cast<PyObject*>(PyImport_AddModule("gc")), "get_referrers", "O", prev_obj);

        if (referrers == nullptr || !PyList_Check(referrers)) {
            Py_XDECREF(referrers);
            PyErr_Clear();
            break;
        }

        bool found_root = false;
        // First unvisited GC-tracked referrer to follow if no root is found at
        // this depth.  We scan ALL referrers before committing to this fallback
        // so that a Frame or Module anywhere in the list is preferred over an
        // arbitrary GC container that happens to appear earlier.
        PyObject* next_obj = nullptr;
        const Py_ssize_t nref = PyList_GET_SIZE(referrers);

        for (Py_ssize_t i = 0; i < nref; ++i) {
            PyObject* ref = PyList_GET_ITEM(referrers, i); // borrowed

            // Skip the referrers list itself, the snapshot objs list (which
            // references every GC-tracked object and was created after
            // gc_id_set was built so it looks like a non-GC referrer), and
            // any already-visited node.
            if (ref == referrers || ref == snapshot_objs) {
                continue;
            }
            const uintptr_t ref_id = reinterpret_cast<uintptr_t>(ref);
            if (visited.count(ref_id)) {
                continue;
            }

            // Frame -> Stack root
            if (PyFrame_Check(ref)) {
                root.category = 'K';

                PyObject* f_code = PyObject_GetAttrString(ref, "f_code");
                if (f_code != nullptr) {
                    PyObject* co_name = PyObject_GetAttrString(f_code, "co_name");
                    PyObject* co_filename = PyObject_GetAttrString(f_code, "co_filename");
                    PyObject* f_lineno = PyObject_GetAttrString(ref, "f_lineno");

                    std::string func = pystr_to_std(co_name);
                    std::string file = pystr_to_std(co_filename);
                    int lineno =
                      (f_lineno != nullptr && PyLong_Check(f_lineno)) ? static_cast<int>(PyLong_AsLong(f_lineno)) : 0;

                    auto slash_pos = file.rfind('/');
                    if (slash_pos != std::string::npos) {
                        file = file.substr(slash_pos + 1);
                    }

                    root.fn = func;
                    root.fn += " (";
                    root.fn += file;
                    root.fn += ":";
                    root.fn += std::to_string(lineno);
                    root.fn += ")";

                    Py_XDECREF(co_name);
                    Py_XDECREF(co_filename);
                    Py_XDECREF(f_lineno);
                    Py_DECREF(f_code);
                }
                PyErr_Clear();
                found_root = true;
                break;
            }

            // Module -> Static root
            if (PyModule_Check(ref)) {
                root.category = 'S';

                PyObject* mod_name = PyObject_GetAttrString(ref, "__name__");
                std::string mname = pystr_to_std(mod_name);
                Py_XDECREF(mod_name);
                PyErr_Clear();

                // Find the attribute of the module that points to prev_obj
                PyObject* mod_dict = PyObject_GetAttrString(ref, "__dict__");
                if (mod_dict != nullptr && PyDict_Check(mod_dict)) {
                    PyObject* key;
                    PyObject* val;
                    Py_ssize_t pos = 0;
                    while (PyDict_Next(mod_dict, &pos, &key, &val)) {
                        if (val == prev_obj) {
                            root.fn = mname + "." + pystr_to_std(key);
                            break;
                        }
                    }
                }
                if (root.fn.empty()) {
                    root.fn = mname;
                }
                Py_XDECREF(mod_dict);
                PyErr_Clear();
                found_root = true;
                break;
            }

            // Referrer is outside the GC-tracked set: treat as Other
            if (gc_id_set.find(ref_id) == gc_id_set.end()) {
                root.category = 'O';
                found_root = true;
                break;
            }

            // Remember the first unvisited GC-tracked referrer as a fallback
            // walk target; keep scanning for a better (Frame/Module) root.
            if (next_obj == nullptr) {
                next_obj = ref;
            }
        }

        Py_DECREF(referrers);
        PyErr_Clear();

        if (found_root || next_obj == nullptr) {
            break;
        }

        // No terminal root at this depth; record this intermediate node and
        // walk up through it.
        visited.insert(reinterpret_cast<uintptr_t>(next_obj));
        chain.push_back(get_info(next_obj));
        prev_obj = next_obj;
    }

    // Reverse: chain[0] is now the object at the root end (directly held by
    // the Frame/Module/C-ext anchor), chain[last] is the original suspect.
    std::reverse(chain.begin(), chain.end());

    // Root node's type represents the object at the root end of the chain.
    root.type_idx = chain[0].first;

    // out_path = everything from the root-holder's immediate children down to
    // the suspect; empty when the suspect is directly held by the anchor.
    out_path.assign(chain.begin() + 1, chain.end());

    return root;
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

// Insert a path of (type_idx, size) pairs as children into `parent`.
// Existing children with the same type_idx at the same depth are merged
// (ic and ts accumulated); new type entries create new child nodes.
void
insert_into_tree(TreeNode& parent, const std::vector<std::pair<uint32_t, uint64_t>>& path, size_t depth)
{
    if (depth >= path.size()) {
        return;
    }
    const uint32_t tidx = path[depth].first;
    const uint64_t sz = path[depth].second;
    for (auto& child : parent.children) {
        if (child.type_idx == tidx) {
            child.add(1, sz);
            insert_into_tree(child, path, depth + 1);
            return;
        }
    }
    TreeNode node;
    node.type_idx = tidx;
    node.ic = 1;
    node.ts = sz;
    parent.children.push_back(std::move(node));
    insert_into_tree(parent.children.back(), path, depth + 1);
}

// Returns true when the type name matches a pattern that is known to produce
// noisy / unactionable leak candidates.  Objects of these types are still
// tracked for survivor-count purposes but are never promoted to the suspect
// list that gets serialised.
bool
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
GCMonitor::start(uint64_t interval_ms,
                 int survivor_threshold,
                 int top_n,
                 bool referrers_enabled,
                 int stability_threshold)
{
    std::unique_lock<std::mutex> lock(_mutex);
    if (_started) {
        return;
    }
    _interval_ms = interval_ms;
    _survivor_threshold = survivor_threshold;
    _top_n = top_n;
    _referrers_enabled = referrers_enabled;
    _stability_threshold = stability_threshold;
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

    PyGILState_STATE gstate = PyGILState_Ensure();
    const auto t_gc_stats_start = Clock::now();

    // ------------------------------------------------------------------
    // 1. GC engine stats
    // ------------------------------------------------------------------
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

    // ------------------------------------------------------------------
    // 2. Enumerate all GC-tracked objects
    // ------------------------------------------------------------------
    PyObject* sys_mod = PyImport_ImportModule("sys");

    PyObject* objs = PyObject_CallMethod(gc_mod, "get_objects", nullptr);
    if (objs == nullptr || !PyList_Check(objs)) {
        Py_XDECREF(objs);
        Py_DECREF(gc_mod);
        Py_XDECREF(sys_mod);
        PyErr_Clear();
        PyGILState_Release(gstate);
        return;
    }

    Py_ssize_t n_objs = PyList_GET_SIZE(objs);

    const auto t_type_scan_start = Clock::now();
    timing.get_objects_us = elapsed_us(t_get_objects_start, t_type_scan_start);

    // Build the GC id set (needed for root detection)
    std::unordered_set<uintptr_t> gc_id_set;
    gc_id_set.reserve(static_cast<size_t>(n_objs));
    for (Py_ssize_t i = 0; i < n_objs; ++i) {
        gc_id_set.insert(reinterpret_cast<uintptr_t>(PyList_GET_ITEM(objs, i)));
    }

    // Build type string table and current object map
    std::vector<std::string> type_table;
    std::unordered_map<std::string, uint32_t> type_table_index;
    // object id -> (type_idx, shallow_size)
    std::unordered_map<uintptr_t, std::pair<uint32_t, uint64_t>> cur_objs;
    cur_objs.reserve(static_cast<size_t>(n_objs));

    for (Py_ssize_t i = 0; i < n_objs; ++i) {
        PyObject* o = PyList_GET_ITEM(objs, i); // borrowed
        uintptr_t oid = reinterpret_cast<uintptr_t>(o);

        std::string tname = type_name_of(o);
        uint32_t tidx;
        auto it = type_table_index.find(tname);
        if (it == type_table_index.end()) {
            tidx = static_cast<uint32_t>(type_table.size());
            type_table.push_back(tname);
            type_table_index[tname] = tidx;
        } else {
            tidx = it->second;
        }

        uint64_t sz = 0;
        if (sys_mod != nullptr) {
            PyObject* size_res = PyObject_CallMethod(sys_mod, "getsizeof", "O", o);
            if (size_res != nullptr && PyLong_Check(size_res)) {
                sz = static_cast<uint64_t>(PyLong_AsUnsignedLongLong(size_res));
            }
            Py_XDECREF(size_res);
            PyErr_Clear();
        }

        cur_objs[oid] = { tidx, sz };
    }

    // Build per-type instance counts (parallel to type_table)
    std::vector<uint32_t> type_counts(type_table.size(), 0);
    for (const auto& [oid, info] : cur_objs) {
        type_counts[info.first]++;
    }

    const auto t_survivor_start = Clock::now();
    timing.type_scan_us = elapsed_us(t_type_scan_start, t_survivor_start);

    // ------------------------------------------------------------------
    // 3a. Update plateau-detection streaks (per type name)
    // ------------------------------------------------------------------
    // For each type seen this snapshot, compare its count against the
    // previous snapshot.  If the count did not grow, increment the stable
    // streak; if it grew, reset to zero.  Types whose streak reaches
    // _stability_threshold are excluded from the suspect list below.
    // A threshold of 0 disables this filter entirely.
    if (_stability_threshold > 0) {
        for (size_t ti = 0; ti < type_table.size(); ++ti) {
            const std::string& tname = type_table[ti];
            const uint32_t cur_count = (ti < type_counts.size()) ? type_counts[ti] : 0;
            if (cur_count == 0) {
                continue;
            }
            const auto prev_it = _prev_type_counts.find(tname);
            const uint32_t prev_count = (prev_it != _prev_type_counts.end()) ? prev_it->second : 0;
            auto& streak = _type_stable_streak[tname];
            if (cur_count > prev_count) {
                // Count grew -- type is (re)gaining momentum, reset streak.
                streak = 0;
            } else {
                ++streak;
            }
            _prev_type_counts[tname] = cur_count;
        }
    }

    // ------------------------------------------------------------------
    // 3. Update survivor counts
    // ------------------------------------------------------------------
    std::unordered_map<uintptr_t, int> new_survivor_counts;
    new_survivor_counts.reserve(_survivor_counts.size());

    for (const auto& [oid, info] : cur_objs) {
        auto prev_it = _prev_objs.find(oid);
        if (prev_it != _prev_objs.end()) {
            // Object was present in the previous snapshot: increment survivor count
            auto sc_it = _survivor_counts.find(oid);
            int count = (sc_it != _survivor_counts.end()) ? sc_it->second + 1 : 1;
            new_survivor_counts[oid] = count;
        }
        // Objects not in _prev_objs start fresh (count = 0) -- not added to new_survivor_counts
    }
    _survivor_counts = std::move(new_survivor_counts);
    _prev_objs = cur_objs;

    const auto t_referrers_start = Clock::now();
    timing.survivor_update_us = elapsed_us(t_survivor_start, t_referrers_start);

    // ------------------------------------------------------------------
    // 4. Build reference tree for suspects (if referrers are enabled)
    // ------------------------------------------------------------------
    // Collect suspects: objects whose survivor count >= threshold
    struct Suspect
    {
        uintptr_t oid;
        uint32_t type_idx;
        uint64_t size;
        int survived;
    };
    std::vector<Suspect> suspects;
    suspects.reserve(static_cast<size_t>(_top_n));

    for (const auto& [oid, sc] : _survivor_counts) {
        if (sc >= _survivor_threshold) {
            auto it = cur_objs.find(oid);
            if (it == cur_objs.end()) {
                continue;
            }
            const std::string& tname = type_table[it->second.first];
            if (is_excluded_type(tname)) {
                continue;
            }
            // Skip types whose instance count has plateaued: they represent
            // stable pools (caches, singletons) rather than growing leaks.
            if (_stability_threshold > 0) {
                const auto streak_it = _type_stable_streak.find(tname);
                if (streak_it != _type_stable_streak.end() && streak_it->second >= _stability_threshold) {
                    continue;
                }
            }
            suspects.push_back({ oid, it->second.first, it->second.second, sc });
        }
    }

    // Sort suspects by survived count (descending), then size (descending)
    std::sort(suspects.begin(), suspects.end(), [](const Suspect& a, const Suspect& b) {
        if (a.survived != b.survived) {
            return a.survived > b.survived;
        }
        return a.size > b.size;
    });

    if (static_cast<int>(suspects.size()) > _top_n) {
        suspects.resize(static_cast<size_t>(_top_n));
    }

    std::vector<RootNode> roots;

    if (_referrers_enabled && !suspects.empty()) {
        // Walk referrer chains to find roots and build the reference tree.
        for (const auto& suspect : suspects) {
            // NOLINTNEXTLINE(performance-no-int-to-ptr)
            PyObject* obj = reinterpret_cast<PyObject*>(suspect.oid);
            // Verify the object is still in the list before walking
            // (the gc_id_set was built from the same objs list, so this is safe)
            if (gc_id_set.find(suspect.oid) == gc_id_set.end()) {
                continue;
            }

            std::vector<std::pair<uint32_t, uint64_t>> out_path;
            RootNode root = find_root(obj,
                                      suspect.type_idx,
                                      suspect.size,
                                      20,
                                      gc_id_set,
                                      cur_objs,
                                      type_table,
                                      type_table_index,
                                      objs,
                                      out_path);

            // Merge into existing roots with same category + fn + type_idx.
            // root.ic / root.ts carry the suspect's count / size so that the
            // root node's aggregates reflect all suspects anchored here.
            bool merged = false;
            for (auto& existing : roots) {
                if (existing.category == root.category && existing.fn == root.fn &&
                    existing.type_idx == root.type_idx) {
                    existing.add(root.ic, root.ts);
                    insert_into_tree(existing, out_path, 0);
                    merged = true;
                    break;
                }
            }
            if (!merged) {
                if (!out_path.empty()) {
                    insert_into_tree(root, out_path, 0);
                }
                roots.push_back(std::move(root));
            }
        }
    } else {
        // Without referrer walking: emit one '?' root per suspect type
        // aggregating all suspects of the same type
        std::unordered_map<uint32_t, RootNode> by_type;
        for (const auto& suspect : suspects) {
            auto it = by_type.find(suspect.type_idx);
            if (it == by_type.end()) {
                RootNode r;
                r.type_idx = suspect.type_idx;
                r.category = '?';
                r.ic = 1;
                r.ts = suspect.size;
                by_type[suspect.type_idx] = std::move(r);
            } else {
                it->second.ic++;
                it->second.ts += suspect.size;
            }
        }
        roots.reserve(by_type.size());
        for (auto& [tidx, rnode] : by_type) {
            roots.push_back(std::move(rnode));
        }
    }

    // find_root may insert new entries into type_table for intermediate objects
    // allocated after gc.get_objects was called.  Pad type_counts so that the
    // parallel arrays tt and tc stay the same length in the output JSON.
    type_counts.resize(type_table.size(), 0);

    Py_DECREF(objs);
    Py_DECREF(gc_mod);
    Py_XDECREF(sys_mod);
    PyErr_Clear();

    PyGILState_Release(gstate);

    const auto t_serialize_start = Clock::now();
    timing.referrers_us = elapsed_us(t_referrers_start, t_serialize_start);

    // ------------------------------------------------------------------
    // 5. Serialize (GIL released)
    // ------------------------------------------------------------------
    serialize(gen_stats, delta_stats, gc_enabled, thresholds, garbage_count, type_table, type_counts, roots);

    const auto t_wall_end = Clock::now();
    timing.serialize_us = elapsed_us(t_serialize_start, t_wall_end);
    timing.wall_us = elapsed_us(t_wall_start, t_wall_end);

    // Push timing into ProfilerStats under the profile mutex, matching the
    // same pattern used by the stack sampler's background thread.
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
