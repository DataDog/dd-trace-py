#define Py_BUILD_CORE 1
#define Py_BUILD_CORE_MODULE 1

#include <Python.h>
#include <frameobject.h>
#include <objimpl.h>

#include "gc_monitor.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <ctime>
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
        // Interpreter-wide "node eaters". Their reference graphs span most of
        // the heap (a module's globals, a type's MRO/__dict__, a function's
        // globals + closure, a frame's locals), so as *roots* they dominate the
        // forest without isolating an actionable application leak. They are
        // still emitted as children wherever an application type references
        // them, so chains like "MyType -> dict -> function" are preserved.
        "function",
        "builtin_function_or_method",
        "code",
        "cell",
        "frame",
        "module",
        "type",
        "mappingproxy",
        "classmethod",
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

// Resolve a PyTypeObject* histogram into a parallel (type_table, type_counts)
// representation, deduplicating by fully-qualified type name.  This is the only
// part of the snapshot that needs Python API calls per *unique type* (not per
// instance), so it is cheap.  GIL must be held.
void
resolve_type_histogram(const std::unordered_map<PyTypeObject*, uint32_t>& type_hist,
                       std::vector<std::string>& type_table,
                       std::vector<uint32_t>& type_counts)
{
    std::unordered_map<std::string, uint32_t> type_table_index;
    type_table.reserve(type_hist.size());
    type_counts.reserve(type_hist.size());

    for (const auto& [tp, count] : type_hist) {
        std::string tname;
        if (tp != nullptr) {
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
}

// ---------------------------------------------------------------------------
// Reference-tree construction (gc.get_referents based)
// ---------------------------------------------------------------------------

// Safety bounds for the unrolled tree.  The type-reference graph is cyclic and
// densely connected (builtin containers reference, and are referenced by, most
// types), so even with a per-path cycle guard an unbounded expansion could blow
// up memory and JSON size.  These caps keep the output bounded regardless of
// heap shape.
constexpr size_t kRefTreeMaxChildren = 64;        // top children (by bytes) per node
constexpr size_t kRefTreeMaxNodes = 100'000;      // total nodes across the forest
constexpr size_t kRefTreeMaxNodesPerRoot = 4'096; // hard cap on a single root's subtree
constexpr size_t kRefTreeMinNodesPerRoot = 16;    // nodes reserved for *every* root

// {reference count, total shallow bytes} aggregated for one (holder, held) edge.
using EdgeAgg = std::pair<uint64_t, uint64_t>;
using AdjList = std::vector<std::pair<uint32_t, EdgeAgg>>;
using Adj = std::unordered_map<uint32_t, AdjList>;

// Default shallow size of an object, mirroring object.__sizeof__: tp_basicsize
// plus the variable part for var-sized types.  This intentionally omits the GC
// header that sys.getsizeof adds; it is a cheap C-only approximation that
// avoids a Python call per object.  GIL must be held (reads Py_TYPE/Py_SIZE).
uint64_t
shallow_size(PyObject* obj) noexcept
{
    PyTypeObject* tp = Py_TYPE(obj);
    Py_ssize_t size = tp->tp_basicsize;
    if (tp->tp_itemsize != 0) {
        Py_ssize_t n = Py_SIZE(obj);
        if (n > 0) {
            size += n * tp->tp_itemsize;
        }
    }
    return size > 0 ? static_cast<uint64_t>(size) : 0;
}

// Recursively unroll the aggregated type graph into a tree rooted at `idx`.
// `on_path` marks the types on the current root-to-node path so a type is never
// expanded twice along the same chain (cycle guard).  `budget` caps the total
// number of nodes emitted across the whole forest.
void
build_subtree(uint32_t idx,
              uint64_t ic,
              uint64_t ts,
              int depth,
              int max_depth,
              const Adj& adj,
              std::vector<char>& on_path,
              size_t& budget,
              TreeNode& out)
{
    out.type_idx = idx;
    out.ic = ic;
    out.ts = ts;
    if (depth >= max_depth) {
        return;
    }
    auto it = adj.find(idx);
    if (it == adj.end()) {
        return;
    }
    on_path[idx] = 1;
    size_t added = 0;
    for (const auto& [child, agg] : it->second) {
        if (added >= kRefTreeMaxChildren || budget == 0) {
            break;
        }
        if (on_path[child] != 0) {
            continue; // type already on this path -- stop to avoid a cycle
        }
        --budget;
        out.children.emplace_back();
        build_subtree(child, agg.first, agg.second, depth + 1, max_depth, adj, on_path, budget, out.children.back());
        ++added;
    }
    on_path[idx] = 0;
}

// Walk every live (GC-tracked) object in `objs`, calling gc.get_referents on
// each to discover which types its instances hold references to, and aggregate
// the result into a "holder type -> held type" graph.  The graph is then
// unrolled into a forest of trees (one per type that has live instances), so
// that following a chain root -> child -> grandchild reads as "instances of the
// root type hold references to instances of the child type, which in turn hold
// references to instances of the grandchild type", together with how many such
// references exist and how many shallow bytes they retain.
//
// Builds `type_table`/`type_counts` (instance counts) from scratch so that held
// types that are not themselves GC-tracked (e.g. int/str) still get a name and
// an index.  GIL must be held for the entire call.
void
build_reference_tree(PyObject* gc_mod,
                     PyObject* objs,
                     int max_depth,
                     std::vector<std::string>& type_table,
                     std::vector<uint32_t>& type_counts,
                     std::vector<TreeNode>& forest)
{
    PyObject* get_referents = PyObject_GetAttrString(gc_mod, "get_referents");
    if (get_referents == nullptr) {
        PyErr_Clear();
        return;
    }

    std::vector<uint64_t> type_sizes;
    std::unordered_map<std::string, uint32_t> name_index;
    std::unordered_map<PyTypeObject*, uint32_t> ptr_index;

    // Resolve a type to its index in the parallel type_table/type_counts/
    // type_sizes vectors, deduplicating by fully-qualified name.  Cached per
    // PyTypeObject* so the (allocating) name lookup runs once per unique type.
    auto intern = [&](PyTypeObject* tp) -> uint32_t {
        auto pit = ptr_index.find(tp);
        if (pit != ptr_index.end()) {
            return pit->second;
        }
        auto* tp_obj = reinterpret_cast<PyObject*>(tp);
        PyObject* mod = PyObject_GetAttrString(tp_obj, "__module__");
        PyObject* qname = PyObject_GetAttrString(tp_obj, "__qualname__");
        std::string mod_s = pystr_to_std(mod);
        std::string qname_s = pystr_to_std(qname);
        Py_XDECREF(mod);
        Py_XDECREF(qname);
        PyErr_Clear();

        std::string tname;
        if (mod_s.empty() || mod_s == "builtins") {
            tname = qname_s.empty() ? "<unknown>" : qname_s;
        } else if (qname_s.empty()) {
            tname = mod_s;
        } else {
            tname = mod_s + "." + qname_s;
        }

        uint32_t idx;
        auto nit = name_index.find(tname);
        if (nit != name_index.end()) {
            idx = nit->second;
        } else {
            idx = static_cast<uint32_t>(type_table.size());
            type_table.push_back(tname);
            type_counts.push_back(0);
            type_sizes.push_back(0);
            name_index.emplace(std::move(tname), idx);
        }
        ptr_index.emplace(tp, idx);
        return idx;
    };

    std::unordered_map<uint64_t, EdgeAgg> edges;

    Py_ssize_t n_objs = PyList_GET_SIZE(objs);
    for (Py_ssize_t i = 0; i < n_objs; ++i) {
        PyObject* obj = PyList_GET_ITEM(objs, i); // borrowed
        uint32_t hidx = intern(Py_TYPE(obj));
        type_counts[hidx] += 1;
        type_sizes[hidx] += shallow_size(obj);

        PyObject* refs = PyObject_CallFunctionObjArgs(get_referents, obj, nullptr);
        if (refs != nullptr && PyList_Check(refs)) {
            Py_ssize_t n_refs = PyList_GET_SIZE(refs);
            for (Py_ssize_t j = 0; j < n_refs; ++j) {
                PyObject* r = PyList_GET_ITEM(refs, j); // borrowed
                if (r == nullptr) {
                    continue;
                }
                uint32_t tidx = intern(Py_TYPE(r));
                uint64_t key = (static_cast<uint64_t>(hidx) << 32) | tidx;
                auto& agg = edges[key];
                agg.first += 1;
                agg.second += shallow_size(r);
            }
        }
        Py_XDECREF(refs);
        PyErr_Clear();
    }

    Py_DECREF(get_referents);

    // Build the adjacency list, keeping each holder's edges sorted by retained
    // bytes (desc) so the most memory-significant children come first -- they
    // are the ones that survive the per-node child cap and node budget.
    Adj adj;
    adj.reserve(edges.size());
    for (const auto& [key, agg] : edges) {
        auto h = static_cast<uint32_t>(key >> 32);
        auto t = static_cast<uint32_t>(key & 0xffffffffULL);
        adj[h].emplace_back(t, agg);
    }
    for (auto& [h, lst] : adj) {
        std::sort(
          lst.begin(), lst.end(), [](const auto& a, const auto& b) { return a.second.second > b.second.second; });
    }

    // Roots: every type with at least one live instance, minus the
    // infrastructure / builtin "node eaters" filtered by is_excluded_type (a
    // densely connected type such as "function" or "module" would otherwise
    // consume the entire node budget by itself and crowd out every application
    // type). Excluded types still appear as children of the types that
    // reference them, so the relationships are not lost.
    std::vector<uint32_t> root_order;
    root_order.reserve(type_counts.size());
    for (uint32_t idx = 0; idx < type_counts.size(); ++idx) {
        if (type_counts[idx] > 0 && !is_excluded_type(type_table[idx])) {
            root_order.push_back(idx);
        }
    }
    // Heaviest first so the shared bonus budget below is spent on the types that
    // retain the most memory.
    std::sort(
      root_order.begin(), root_order.end(), [&](uint32_t a, uint32_t b) { return type_sizes[a] > type_sizes[b]; });

    // Budget policy. A single global cap lets one densely connected root eat the
    // whole forest, starving every other type (the bug that hid application
    // types from "rt"). Instead we split the global cap into:
    //   * a per-root *floor* (kRefTreeMinNodesPerRoot) reserved for every root
    //     up front, so even small, low-ranked application types -- the ones that
    //     usually reveal a leak -- are guaranteed a subtree; and
    //   * a shared *bonus* pool the heaviest roots draw from, each limited to
    //     kRefTreeMaxNodesPerRoot total, so no single root can monopolise it.
    // Every root therefore always appears, and total nodes stay <= kRefTreeMaxNodes.
    const size_t n_roots = root_order.size();
    std::vector<char> on_path(type_table.size(), 0);
    forest.reserve(n_roots);

    if (n_roots != 0) {
        size_t floor = kRefTreeMinNodesPerRoot;
        // If the floors alone would exceed the global cap, shrink the floor so
        // every root still gets at least one node.
        if (floor * n_roots > kRefTreeMaxNodes) {
            floor = std::max<size_t>(1, kRefTreeMaxNodes / n_roots);
        }
        const size_t reserved = floor * n_roots;
        size_t bonus_pool = kRefTreeMaxNodes > reserved ? kRefTreeMaxNodes - reserved : 0;

        for (uint32_t idx : root_order) {
            // Total nodes this root may grow to: its reserved floor plus
            // whatever it can claim from the shared bonus pool, capped overall.
            const size_t headroom = kRefTreeMaxNodesPerRoot > floor ? kRefTreeMaxNodesPerRoot - floor : 0;
            const size_t cap = floor + std::min(bonus_pool, headroom);

            // build_subtree always emits the root node itself (it sets the node
            // fields before consulting the budget) and decrements the budget per
            // *child*, so the children budget is cap minus the root node.
            size_t remaining = cap > 0 ? cap - 1 : 0;
            forest.emplace_back();
            build_subtree(idx, type_counts[idx], type_sizes[idx], 0, max_depth, adj, on_path, remaining, forest.back());

            // Account for the bonus actually spent; the floor portion is
            // per-root and not returned to the shared pool.
            const size_t used = cap - remaining; // root node + children emitted
            const size_t bonus_used = used > floor ? used - floor : 0;
            bonus_pool -= bonus_used;
        }
    }
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
GCMonitor::start(uint64_t interval_ms, int survivor_threshold, int top_n, bool referrers_enabled, int max_depth)
{
    std::unique_lock<std::mutex> lock(_mutex);
    if (_started) {
        return;
    }
    _interval_ms = interval_ms;
    _survivor_threshold = survivor_threshold;
    _top_n = top_n;
    _referrers_enabled = referrers_enabled;
    _max_depth = max_depth > 0 ? max_depth : 1;
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

    std::vector<std::string> type_table;
    std::vector<uint32_t> type_counts;
    std::vector<TreeNode> ref_tree;
    Clock::time_point t_name_resolve_start;

    if (_referrers_enabled) {
        // --------------------------------------------------------------
        // Reference-tree mode (GIL held for the whole walk).
        //
        // gc.get_objects() materializes the live object list and owns a
        // reference to every object, keeping them alive for the duration of the
        // walk.  We then call gc.get_referents() on each object to learn which
        // types its instances point at, aggregate the result into a "holder
        // type -> held type" graph and unroll it into a forest of type trees.
        // This is much heavier than the refcount-free type tally below (a Python
        // call plus a temporary list per object), so it is gated behind the
        // referrers flag.
        // --------------------------------------------------------------
        PyObject* objs = PyObject_CallMethod(gc_mod, "get_objects", nullptr);
        if (objs == nullptr || !PyList_Check(objs)) {
            Py_XDECREF(objs);
            Py_DECREF(gc_mod);
            PyErr_Clear();
            PyGILState_Release(gstate);
            return;
        }

        const auto t_walk_start = Clock::now();
        timing.get_objects_us = elapsed_us(t_get_objects_start, t_walk_start);

        build_reference_tree(gc_mod, objs, _max_depth, type_table, type_counts, ref_tree);

        Py_DECREF(objs);
        Py_DECREF(gc_mod);
        PyErr_Clear();

        t_name_resolve_start = Clock::now();
        timing.type_scan_us = elapsed_us(t_walk_start, t_name_resolve_start);
        PyGILState_Release(gstate);
    } else {
        std::unordered_map<PyTypeObject*, uint32_t> type_hist;
#if PY_VERSION_HEX >= 0x030C0000
        // --------------------------------------------------------------
        // Phases 1+2 (GIL held): walk all GC-tracked objects in place and tally
        // them by type.
        //
        // PyUnstable_GC_VisitObjects (3.12+) iterates the GC heaps without
        // allocating a Python list and without touching refcounts, unlike
        // gc.get_objects() which builds an N-element list and performs 2N
        // INCREF/DECREF operations.  The callback only reads Py_TYPE and updates
        // a C++ hashmap -- it must not (de)allocate Python objects or trigger a
        // collection (GC is disabled for the duration of the visit anyway).  We
        // never store the object pointers, only their type pointers, so there is
        // no lifetime concern once the visit returns.
        // --------------------------------------------------------------
        Py_DECREF(gc_mod); // not needed for the in-place walk

        PyUnstable_GC_VisitObjects(
          [](PyObject* obj, void* arg) noexcept -> int {
              auto* hist = static_cast<std::unordered_map<PyTypeObject*, uint32_t>*>(arg);
              try {
                  (*hist)[Py_TYPE(obj)]++;
              } catch (...) {
                  // A C++ exception must never propagate into CPython's C frames.
                  // On allocation failure we simply drop this object's tally.
              }
              return 1; // continue iteration
          },
          &type_hist);

        // --------------------------------------------------------------
        // Phase 3 (GIL still held): resolve type names per unique type.  The
        // PyTypeObject* keys are guaranteed live here: we hold the GIL and have
        // not allocated/freed anything since the visit, so every type that had a
        // live instance during the walk is still alive.
        // --------------------------------------------------------------
        t_name_resolve_start = Clock::now();
        timing.get_objects_us = elapsed_us(t_get_objects_start, t_name_resolve_start);
        timing.type_scan_us = 0; // the type tally is folded into the visit above

        resolve_type_histogram(type_hist, type_table, type_counts);

        PyErr_Clear();
        PyGILState_Release(gstate);
#else
        // --------------------------------------------------------------
        // Fallback for Python < 3.12, which lacks PyUnstable_GC_VisitObjects.
        //
        // Phase 1 (GIL held): gc.get_objects() materializes the live object list.
        // --------------------------------------------------------------
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

        // --------------------------------------------------------------
        // Phase 2 (GIL released): build type histogram
        //
        // We read ob_type for each object and tally counts by PyTypeObject*.
        // No Python API calls, no refcount operations -- just a struct-field
        // read and a hashmap update.  Python threads are free to run while we
        // do the O(n) work here.
        // --------------------------------------------------------------
        type_hist.reserve(static_cast<size_t>(n_objs / 8));

        for (PyObject* obj : ptrs) {
            type_hist[Py_TYPE(obj)]++;
        }

        // --------------------------------------------------------------
        // Phase 3 (GIL re-acquired): resolve type names, release object list
        //
        // We resolve names while `objs` is still alive to guarantee that the
        // PyTypeObject* keys in type_hist are valid (a heap type's refcount
        // includes a contribution from each of its live instances).
        // --------------------------------------------------------------
        gstate = PyGILState_Ensure();
        t_name_resolve_start = Clock::now();
        timing.type_scan_us = elapsed_us(t_type_scan_start, t_name_resolve_start);

        resolve_type_histogram(type_hist, type_table, type_counts);

        // Release the object list now that we no longer need the type pointers.
        Py_DECREF(objs);
        PyErr_Clear();
        PyGILState_Release(gstate);
#endif
    }

    // ------------------------------------------------------------------
    // Phase 4 (GIL released): serialize
    // ------------------------------------------------------------------
    const auto t_serialize_start = Clock::now();
    timing.name_resolve_us = elapsed_us(t_name_resolve_start, t_serialize_start);

    std::vector<RootNode> roots;
    serialize(gen_stats, delta_stats, gc_enabled, thresholds, garbage_count, type_table, type_counts, roots, ref_tree);

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
                     const std::vector<RootNode>& roots,
                     const std::vector<TreeNode>& ref_tree)
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
    out << "]";

    // reference tree (type -> type "holds a reference to" graph). Each node is
    // {"t":type_idx,"ic":refs,"ts":bytes,"ch":[...]} -- see serialize_node. A
    // root node's ic/ts are the type's live instance count and total shallow
    // size; a child node's ic/ts are the number of references from the parent
    // type to the child type and the shallow bytes they retain.
    out << ",\"rt\":[";
    for (size_t i = 0; i < ref_tree.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        serialize_node(out, ref_tree[i], 0);
    }
    out << "]}";

    std::lock_guard<std::mutex> lock(_mutex);
    _latest_json = out.str();
}

} // namespace Datadog
