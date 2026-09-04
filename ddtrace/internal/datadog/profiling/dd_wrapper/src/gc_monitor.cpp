#define Py_BUILD_CORE 1
#define Py_BUILD_CORE_MODULE 1

#include <Python.h>
#include <frameobject.h>
#include <objimpl.h>

#include "gc_monitor.hpp"
#include "gc_monitor_json.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <ctime>
#include <pthread.h>
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

    return "";
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
// part of the snapshot that needs Python API calls (once per unique type, (not per
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
                tname = mod_s;
                tname += "." + qname_s;
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
// Reference-graph construction (gc.get_referents based)
// ---------------------------------------------------------------------------

// The type-reference graph is cyclic and densely connected (builtin containers
// reference, and are referenced by, most types). We emit the first-order graph
// (each holder type's direct edges) and leave any tree reconstruction to the
// consumer, so no unbounded unrolling happens natively. The only bound here is
// the per-holder fan-out: keep the heaviest edges (by retained bytes) so a hub
// type like dict/type cannot produce a pathologically wide adjacency row.
constexpr size_t kRefGraphMaxEdgesPerType = 64; // top edges (by bytes) per holder type

// Bounds for the object-level graph capture + container-collapse pass. The
// object graph preserves identity (one node per live PyObject*) so the collapse
// can follow real references; these caps keep memory/CPU bounded on huge heaps.
constexpr size_t kMaxObjectGraphEdges = 30'000'000;    // stop recording edges past this (safety valve)
constexpr size_t kCollapseMaxVisitPerObject = 100'000; // max nodes visited collapsing one holder

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

// True for the generic builtin container types whose contents are what an
// application object effectively retains. The collapse pass steps through
// these instead of recording an edge to them, so that a chain like
// "BundleIndex.__dict__ -> {bundle-id dict} -> Bundle" is attributed to
// BundleIndex as "BundleIndex -> Bundle" rather than disappearing into the
// process-wide "dict" tally. The Py*_Check macros also match subclasses
// (defaultdict, OrderedDict, named tuples, ...). GIL must be held.
bool
is_transparent_container(PyObject* obj) noexcept
{
    return PyDict_Check(obj) || PyList_Check(obj) || PyTuple_Check(obj) || PyAnySet_Check(obj);
}

// (No native tree unrolling: the collapsed first-order graph is emitted as an
// adjacency and the consumer reconstructs any tree it needs. See
// build_collapsed_graph below and serialize_snapshot_json.)

// Object-level reference graph captured under the GIL. Identity is preserved
// (one node per live PyObject*) so the collapse pass can follow real object
// references instead of a lossy type->type aggregate that washes container
// contents into the process-wide "dict"/"list" tally.
struct ObjectGraph
{
    std::vector<uint32_t> node_type;                      // type index per node (into type_table)
    std::vector<uint64_t> node_size;                      // shallow size per node
    std::vector<char> node_transparent;                   // 1 if a generic container to step through
    std::vector<std::pair<uint32_t, uint32_t>> raw_edges; // (holder node, referent node)
    std::vector<uint64_t> type_sizes;                     // shallow byte sum per type (parallel to type_table)
};

// Phase A (GIL held): walk every live object in `objs`, calling gc.get_referents
// once per object, and record the object-level reference graph into g
// (preserving identity) plus the per-type instance counts/sizes. This is the
// only phase that touches the Python API; the heavier graph processing runs
// afterwards with the GIL released (build_collapsed_graph).
void
build_object_graph(PyObject* gc_mod,
                   PyObject* objs,
                   std::vector<std::string>& type_table,
                   std::vector<uint32_t>& type_counts,
                   ObjectGraph& g)
{
    PyObject* get_referents = PyObject_GetAttrString(gc_mod, "get_referents");
    if (get_referents == nullptr) {
        PyErr_Clear();
        return;
    }

    std::unordered_map<std::string, uint32_t> name_index;
    std::unordered_map<PyTypeObject*, uint32_t> ptr_index;

    // Resolve a type to its index in the parallel type_table/type_counts/
    // type_sizes vectors, deduplicating by fully-qualified name. Cached per
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
            g.type_sizes.push_back(0);
            name_index.emplace(std::move(tname), idx);
        }
        ptr_index.emplace(tp, idx);
        return idx;
    };

    // Intern one node per unique live PyObject*. Referents that are not in
    // objs (e.g. untracked int/str leaves) still get a node so an edge can
    // point at them, but they are never expanded (we only call get_referents on
    // objects in the authoritative objs list).
    std::unordered_map<PyObject*, uint32_t> obj_index;
    auto add_node = [&](PyObject* o) -> uint32_t {
        auto it = obj_index.find(o);
        if (it != obj_index.end()) {
            return it->second;
        }
        auto idx = static_cast<uint32_t>(g.node_type.size());
        obj_index.emplace(o, idx);
        g.node_type.push_back(intern(Py_TYPE(o)));
        g.node_size.push_back(shallow_size(o));
        g.node_transparent.push_back(is_transparent_container(o) ? 1 : 0);
        return idx;
    };

    Py_ssize_t n_objs = PyList_GET_SIZE(objs);
    obj_index.reserve(static_cast<size_t>(n_objs) * 2);
    g.node_type.reserve(static_cast<size_t>(n_objs));
    g.node_size.reserve(static_cast<size_t>(n_objs));
    g.node_transparent.reserve(static_cast<size_t>(n_objs));

    for (Py_ssize_t i = 0; i < n_objs; ++i) {
        PyObject* obj = PyList_GET_ITEM(objs, i); // borrowed
        uint32_t oi = add_node(obj);
        // Live instance counts/bytes are tallied only for objects in the
        // authoritative get_objects() set, mirroring the type-tally path.
        uint32_t htype = g.node_type[oi];
        type_counts[htype] += 1;
        g.type_sizes[htype] += g.node_size[oi];

        PyObject* refs = PyObject_CallFunctionObjArgs(get_referents, obj, nullptr);
        if (refs != nullptr && PyList_Check(refs)) {
            Py_ssize_t n_refs = PyList_GET_SIZE(refs);
            for (Py_ssize_t j = 0; j < n_refs; ++j) {
                PyObject* r = PyList_GET_ITEM(refs, j); // borrowed
                if (r == nullptr) {
                    continue;
                }
                if (g.raw_edges.size() >= kMaxObjectGraphEdges) {
                    break; // safety valve on pathologically large heaps
                }
                uint32_t ri = add_node(r);
                g.raw_edges.emplace_back(oi, ri);
            }
        }
        Py_XDECREF(refs);
        PyErr_Clear();
        if (g.raw_edges.size() >= kMaxObjectGraphEdges) {
            break;
        }
    }

    Py_DECREF(get_referents);
}

// Phase B (GIL released): collapse generic containers on the object graph and
// build the first-order "holder type -> retained type" adjacency. Because the
// walk follows real object references, container contents are attributed to the
// specific holder that owns them (no process-wide pooling). `adjacency` is
// filled parallel to `type_table` (adjacency[holder] = its edges) and `roots`
// gets the ordered set of types to expose as reconstruction roots. Touches no
// Python API -- ints only.
void
build_collapsed_graph(const ObjectGraph& g,
                      const std::vector<std::string>& type_table,
                      const std::vector<uint32_t>& type_counts,
                      std::vector<std::vector<GCRefEdge>>& adjacency,
                      std::vector<uint32_t>& roots)
{
    const size_t n_nodes = g.node_type.size();
    const size_t n_types = type_table.size();
    if (n_nodes == 0) {
        return;
    }

    // Per-type exclusion flags (string work only -- safe with the GIL released).
    std::vector<char> type_excluded(n_types, 0);
    for (size_t i = 0; i < n_types; ++i) {
        type_excluded[i] = is_excluded_type(type_table[i]) ? 1 : 0;
    }

    // CSR adjacency from the (holder -> referent) edge list via counting sort.
    std::vector<uint32_t> adj_off(n_nodes + 1, 0);
    for (const auto& e : g.raw_edges) {
        adj_off[e.first + 1] += 1;
    }
    for (size_t i = 0; i < n_nodes; ++i) {
        adj_off[i + 1] += adj_off[i];
    }
    std::vector<uint32_t> adj_nodes(g.raw_edges.size());
    {
        std::vector<uint32_t> fill(adj_off.begin(), adj_off.begin() + static_cast<std::ptrdiff_t>(n_nodes));
        for (const auto& e : g.raw_edges) {
            adj_nodes[fill[e.first]++] = e.second;
        }
    }

    // Approximate retained size per opaque object: its own shallow size plus the
    // shallow size of every generic container it reaches through transparent-only
    // paths (its __dict__ and the nested dicts/lists/sets hanging off it),
    // stopping at the next opaque object. Collapsed edges are weighted by this
    // instead of the target's bare header size, so a small wrapper that owns a
    // large container scaffold (e.g. an index object whose 16-byte header fronts
    // many nested dicts/sets) is ranked by what it holds rather than disappearing
    // as the lightest child under a tight node budget. Bounded per object by
    // kCollapseMaxVisitPerObject; a per-source epoch stamp keeps a shared
    // container from being counted twice within one object's walk.
    std::vector<uint64_t> node_retained(n_nodes, 0);
    std::vector<uint64_t> type_retained(n_types, 0);
    {
        std::vector<uint32_t> rseen(n_nodes, 0);
        uint32_t repoch = 0;
        std::vector<uint32_t> rstack;
        for (uint32_t o = 0; o < n_nodes; ++o) {
            if (g.node_transparent[o] != 0) {
                continue; // only opaque objects are ever recorded as collapsed-edge targets
            }
            ++repoch;
            rseen[o] = repoch;
            uint64_t total = g.node_size[o];
            rstack.clear();
            for (uint32_t e = adj_off[o]; e < adj_off[o + 1]; ++e) {
                uint32_t s = adj_nodes[e];
                if (g.node_transparent[s] != 0 && rseen[s] != repoch) {
                    rseen[s] = repoch;
                    rstack.push_back(s);
                }
            }
            size_t visited = 0;
            while (!rstack.empty()) {
                uint32_t cur = rstack.back();
                rstack.pop_back();
                total += g.node_size[cur];
                if (++visited > kCollapseMaxVisitPerObject) {
                    break;
                }
                for (uint32_t e = adj_off[cur]; e < adj_off[cur + 1]; ++e) {
                    uint32_t s = adj_nodes[e];
                    if (g.node_transparent[s] != 0 && rseen[s] != repoch) {
                        rseen[s] = repoch;
                        rstack.push_back(s);
                    }
                }
            }
            node_retained[o] = total;
            type_retained[g.node_type[o]] += total;
        }
    }

    // Collapse: from every opaque (non-container), non-excluded holder object,
    // walk through transparent referents (its __dict__, the dicts/lists/sets it
    // owns, ...) and record an edge holder_type -> first opaque type reached.
    // Stop at opaque objects (their own edges are recorded when *they* are the
    // holder). Edges to excluded "node eater" types are dropped so the tree is
    // pure application data.
    std::unordered_map<uint64_t, EdgeAgg> edges;
    std::vector<uint32_t> seen(n_nodes, 0); // epoch stamp per node (0 = unseen)
    uint32_t epoch = 0;
    std::vector<uint32_t> stack;

    for (uint32_t src = 0; src < n_nodes; ++src) {
        if (g.node_transparent[src] != 0) {
            continue; // containers are stepped through, never a holder root
        }
        uint32_t htype = g.node_type[src];
        if (type_excluded[htype] != 0) {
            continue;
        }
        ++epoch;
        seen[src] = epoch;
        stack.clear();
        for (uint32_t e = adj_off[src]; e < adj_off[src + 1]; ++e) {
            stack.push_back(adj_nodes[e]);
        }
        size_t visited = 0;
        while (!stack.empty()) {
            uint32_t cur = stack.back();
            stack.pop_back();
            if (seen[cur] == epoch) {
                continue;
            }
            seen[cur] = epoch;
            if (++visited > kCollapseMaxVisitPerObject) {
                break;
            }
            if (g.node_transparent[cur] != 0) {
                for (uint32_t e = adj_off[cur]; e < adj_off[cur + 1]; ++e) {
                    uint32_t nxt = adj_nodes[e];
                    if (seen[nxt] != epoch) {
                        stack.push_back(nxt);
                    }
                }
                continue;
            }
            // Reached an opaque object: record the collapsed edge once per
            // holder (the epoch-dedup guarantees once), then stop -- do not
            // descend through application objects. The edge is weighted by the
            // reached object's retained size (self + owned container scaffold),
            // not its bare header, so wrappers that front large scaffolds rank
            // by what they hold.
            uint32_t ttype = g.node_type[cur];
            if (type_excluded[ttype] == 0) {
                uint64_t key = (static_cast<uint64_t>(htype) << 32) | ttype;
                auto& agg = edges[key];
                agg.first += 1;
                agg.second += node_retained[cur];
            }
        }
    }

    // Build the type-level adjacency, each holder's edges sorted by retained
    // bytes (desc) so the most memory-significant children come first.
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

    // Emit the first-order adjacency parallel to the type table, keeping only
    // the heaviest kRefGraphMaxEdgesPerType edges per holder (rows are already
    // sorted by retained bytes desc). No tree is unrolled here; the consumer
    // reconstructs whatever depth it wants from this graph.
    adjacency.assign(n_types, {});
    for (const auto& [h, lst] : adj) {
        auto& row = adjacency[h];
        const size_t keep = std::min<size_t>(lst.size(), kRefGraphMaxEdgesPerType);
        row.reserve(keep);
        for (size_t i = 0; i < keep; ++i) {
            row.push_back(GCRefEdge{ lst[i].first, lst[i].second.first, lst[i].second.second });
        }
    }

    // Roots: every type with live instances, minus the infrastructure / builtin
    // "node eaters" (is_excluded_type). Generic containers are excluded too, so
    // reconstruction is rooted at application types. Heaviest retained first.
    roots.reserve(n_types);
    for (uint32_t idx = 0; idx < n_types; ++idx) {
        if (type_counts[idx] > 0 && type_excluded[idx] == 0) {
            roots.push_back(idx);
        }
    }
    std::sort(roots.begin(), roots.end(), [&](uint32_t a, uint32_t b) { return type_retained[a] > type_retained[b]; });
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
    install_atfork_once();

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
    // We never join; shutdown is signal-only using a condition variable
    _thread.detach();
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

void
GCMonitor::install_atfork_once()
{
    static std::once_flag flag;

    std::call_once(flag, []() {
        pthread_atfork([]() { GCMonitor::get().prefork(); },
                       []() { GCMonitor::get().postfork_parent(); },
                       []() { GCMonitor::get().postfork_child(); });
    });
}

void
GCMonitor::prefork()
{
    // Snapshot the running state *before* locking so postfork_child() can
    // decide whether to re-arm the thread; the placement-new below wipes it.
    _was_running_at_fork = _started;

    // Take the mutex on the forking thread so fork() sees a quiescent snapshot
    // of GCMonitor state. The background thread is either inside _cv.wait_for
    // (mutex released) or inside take_snapshot()/serialize() (mutex briefly
    // held); this call blocks the forker until the sampling thread relinquishes
    // the mutex, guaranteeing consistent copy-on-write state in the child.
    _mutex.lock();
}

void
GCMonitor::postfork_parent()
{
    // The parent's background thread survives the fork unchanged, so simply
    // release the mutex that prefork() acquired and continue monitoring.
    _mutex.unlock();
}

void
GCMonitor::postfork_child()
{
    // Only the forking thread exists in the child. The parent's std::thread
    // is gone but its handle remains in _thread; the mutex/cv may be inherited
    // in an undefined state; and any bookkeeping the background thread was
    // maintaining is stale. Reset every piece of state via placement-new so
    // subsequent stop_gc_monitor()/start_gc_monitor() calls behave the same
    // way they would in a fresh process.
    new (&_thread) std::thread();
    new (&_mutex) std::mutex();
    new (&_cv) std::condition_variable();
    _stop_flag = false;
    _started = false;
    _prev_gen_stats = {};
    _latest_json.clear();

    if (_was_running_at_fork) {
        // Re-arm a fresh background thread with the same configuration the
        // parent was using. The interval/threshold/top_n/... fields survive
        // fork intact because they are plain data.
        _started = true;
        _thread = std::thread(&GCMonitor::thread_main, this);
        _thread.detach();
    }
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

    // Phase 1 (GIL held): GC engine stats + get_objects
    // We keep this section as short as possible.  The only reason we need
    // the GIL here is to call Python API functions.
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
    std::vector<uint64_t> type_sizes;                  // per-type total shallow bytes (referrers only)
    std::vector<std::vector<GCRefEdge>> ref_adjacency; // holder -> edges (referrers only)
    std::vector<uint32_t> ref_roots;                   // ordered reconstruction roots (referrers only)
    Clock::time_point t_name_resolve_start;

    if (_referrers_enabled) {
        // Reference chains enabled
        //
        // Phase A (GIL held): gc.get_objects() materializes the live object list
        // and owns a reference to every object, keeping them alive for the walk.
        // build_object_graph calls gc.get_referents() once per object and
        // records the object-level reference graph (identity preserved). This
        // is the only Python-touching phase, kept as short as possible.
        //
        // Phase B (GIL released): build_collapsed_graph collapses generic
        // containers on the pure-C++ int graph and emits the first-order type
        // adjacency, so the heavy processing does not block other Python
        // threads. Because it follows real object references, container contents
        // are attributed to the specific holder that owns them (e.g. BundleIndex
        // -> Bundle), which a type-only aggregate cannot do. The consumer
        // reconstructs any tree from the adjacency. Gated behind the referrers flag.
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

        ObjectGraph graph;
        build_object_graph(gc_mod, objs, type_table, type_counts, graph);

        Py_DECREF(objs);
        Py_DECREF(gc_mod);
        PyErr_Clear();

        t_name_resolve_start = Clock::now();
        timing.type_scan_us = elapsed_us(t_walk_start, t_name_resolve_start);
        PyGILState_Release(gstate);

        // Phase B runs with the GIL released (no Python API below this point).
        build_collapsed_graph(graph, type_table, type_counts, ref_adjacency, ref_roots);
        type_sizes = std::move(graph.type_sizes);
    } else {
        // Reference chains disabled, we only make a class histogram
        std::unordered_map<PyTypeObject*, uint32_t> type_hist;
#if PY_VERSION_HEX >= 0x030C0000
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
        Py_DECREF(gc_mod); // not needed for the in-place walk

        PyUnstable_GC_VisitObjects(
          [](PyObject* obj, void* arg) noexcept -> int {
              auto* hist = static_cast<std::unordered_map<PyTypeObject*, uint32_t>*>(arg);
              try {
                  (*hist)[Py_TYPE(obj)]++;
              } catch (const std::exception& e) {
                  // A C++ exception must never propagate into CPython's C frames.
                  // On allocation failure we simply drop this object's tally.
                  std::cerr << "Error in PyUnstable_GC_VisitObjects: " << e.what() << std::endl;
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
        // Fallback for Python < 3.12, which lacks PyUnstable_GC_VisitObjects.
        //
        // Phase 1 (GIL held): gc.get_objects() materializes the live object list.
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

        // Phase 2 (GIL released): build type histogram
        //
        // We read ob_type for each object and tally counts by PyTypeObject*.
        // No Python API calls, no refcount operations -- just a struct-field
        // read and a hashmap update.  Python threads are free to run while we
        // do the O(n) work here.
        type_hist.reserve(static_cast<size_t>(n_objs / 8));

        for (PyObject* obj : ptrs) {
            type_hist[Py_TYPE(obj)]++;
        }

        // Phase 3 (GIL re-acquired): resolve type names, release object list
        //
        // We resolve names while `objs` is still alive to guarantee that the
        // PyTypeObject* keys in type_hist are valid (a heap type's refcount
        // includes a contribution from each of its live instances).
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

    // Phase 4 (GIL released): serialize
    const auto t_serialize_start = Clock::now();
    timing.name_resolve_us = elapsed_us(t_name_resolve_start, t_serialize_start);

    serialize(gen_stats,
              delta_stats,
              gc_enabled,
              thresholds,
              garbage_count,
              type_table,
              type_counts,
              type_sizes,
              ref_adjacency,
              ref_roots);

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
                     const std::vector<uint64_t>& type_sizes,
                     const std::vector<std::vector<GCRefEdge>>& adjacency,
                     const std::vector<uint32_t>& roots)
{
    std::string json = serialize_snapshot_json(gen_stats,
                                               delta_stats,
                                               gc_enabled,
                                               thresholds,
                                               garbage_count,
                                               type_table,
                                               type_counts,
                                               type_sizes,
                                               adjacency,
                                               roots);

    std::lock_guard<std::mutex> lock(_mutex);
    _latest_json = std::move(json);
}

} // namespace Datadog
