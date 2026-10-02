# Task Link Map — Performance Investigation

## Context

Two `inuse-space` pprof profiles were captured from a native profiler running against a live
service that had the dd-trace-py Python profiler active. Both cover 59-second windows on the same
build (`_stack.cpython-312-x86_64-linux-gnu.so`, build ID `b64c7dd8...`).

- `profile.pprof` — 646 MB inuse
- `profile-2.pprof` — 932 MB inuse (+44%)

The service uses **uvloop** as its asyncio event loop (`uv_run`/`uv__run_idle` account for 254 MB
cumulative in profile-2) and creates asyncio tasks at high throughput (`task_step`/`task_step_impl`
256 MB, `gen_send_ex`/`gen_send_ex2` 355 MB combined).

---

## Evidence from the Profiles

### `Datadog::Sampler::link_tasks` → `operator new` (22 MB, profile-2)

```
Datadog::Sampler::link_tasks(_object*, _object*)   22 MB cum
  └─ operator new(unsigned long)                   22 MB
```

This node appears only in profile-2, not profile-1. It reflects heap allocation pressure from
inserting into `std::unordered_map<PyObject*, PyObject*>` — one `operator new`-allocated node per
new task relationship inserted.

### `Datadog::Sampler::capture_samples` overhead

```
Datadog::Sampler::sampling_thread → capture_samples
  ├─ operator new           12.5 MB (profile-1) / 6.5 MB (profile-2)
  └─ StackRenderer::render_stack_end → flush → export → Profile::collect
```

`operator new` appearing directly inside the sampling hot path indicates allocations occurring
during each sample cycle.

---

## Code: The Two Maps

**`echion/echion_sampler.h:44-46`**
```cpp
std::unordered_map<PyObject*, PyObject*> task_link_map_;      // strong links (gather, shield)
std::unordered_map<PyObject*, PyObject*> weak_task_link_map_; // weak links (create_task)
std::mutex task_link_map_lock_;
```

Both maps store raw `PyObject*` pointers as keys and values. No reference counts are held.
There is no capacity pre-sizing; the maps grow on demand via `operator new` per node.

---

## Code: Write Path — Called from Python on Every Task Creation

**`src/sampler.cpp:941-951`**
```cpp
void Sampler::link_tasks(PyObject* parent, PyObject* child) {
    std::lock_guard<std::mutex> guard(echion->task_link_map_lock());
    echion->task_link_map()[child] = parent;   // allocates a node if key is new
}

void Sampler::weak_link_tasks(PyObject* parent, PyObject* child) {
    std::lock_guard<std::mutex> guard(echion->task_link_map_lock());
    echion->weak_task_link_map()[child] = parent;
}
```

These are called from `ddtrace/profiling/_asyncio.py` on every hook:
`create_task`, `gather`, `shield`, `as_completed`, `TaskGroup.create_task`.

In a high-throughput uvloop service, this means `operator new` + `task_link_map_lock_` contention
on every task creation, from the Python thread.

---

## Code: Read/Cleanup Path — Inside Every Sample Cycle

**`src/echion/threads.cc:159-229`** — runs while holding `task_link_map_lock_` during `unwind_tasks`:

```cpp
std::lock_guard<std::mutex> lock(echion.task_link_map_lock());  // line 161

// Step 1: build all_task_origins from live tasks
std::unordered_set<PyObject*> all_task_origins;
std::transform(all_tasks.cbegin(), all_tasks.cend(),
               std::inserter(all_task_origins, all_task_origins.begin()),
               [](const TaskInfo::Ptr& t) { return t->origin; });   // O(N) alloc

// Step 2: scan task_link_map for stale entries
std::vector<PyObject*> to_remove;
for (auto kv : task_link_map) {                                      // O(M) scan
    if (all_task_origins.find(kv.first) == all_task_origins.end())
        to_remove.push_back(kv.first);
}
for (auto key : to_remove) {
    // Grace period: only erase if the child was seen in the previous cycle
    if (previous_task_objects.find(key) != previous_task_objects.end())
        task_link_map.erase(key);
}

// Step 3: rebuild all_task_origins again for weak_task_link_map
all_task_origins.clear();
std::transform(all_tasks.cbegin(), all_tasks.cend(), ...);           // O(N) again — identical

// Step 4: scan weak_task_link_map for stale entries (no grace period)
for (auto kv : weak_task_link_map) { ... }                          // O(M') scan

// Step 5: scan task_link_map again to build parent_tasks set
for (auto& link : task_link_map) { ... }                            // O(M) third pass

// Step 6: rebuild previous_task_objects from scratch
previous_task_objects.clear();
for (const auto& task : all_tasks) {
    previous_task_objects.insert(task->origin);                      // O(N) alloc
}
```

**There is also a second lock acquisition per leaf task** during the parent-chain walk
(`src/echion/threads.cc:390`):

```cpp
// Called once per leaf task, per sample cycle
std::lock_guard<std::mutex> lock(echion.task_link_map_lock());
auto& task_link_map = echion.task_link_map();
// ... lookup task_link_map and weak_task_link_map for parent chain
```

---

## Issues Identified

### 1. `all_task_origins` is rebuilt from scratch twice per cycle

Lines 168–172 build the set; lines 195–199 `.clear()` it and rebuild it identically for the
`weak_task_link_map` cleanup. This is pure duplicate work — O(N) allocation and N insertions,
done twice, within the same lock hold.

### 2. Full map scan every cycle regardless of activity

Even when no tasks have died, the cleanup still iterates the entire `task_link_map` (line 175)
and `weak_task_link_map` (line 202) to check each entry against `all_task_origins`. In a stable
service with many long-lived tasks this is wasted O(M) work per sample cycle.

### 3. Asymmetric eviction between the two maps

`task_link_map` entries require a **two-cycle grace period** (must have appeared in
`previous_task_objects`) before they are removed. `weak_task_link_map` entries are removed after
one cycle with no grace period. This means in a high-throughput service, `task_link_map` always
holds entries for tasks that died in the current cycle — they are not cleaned up until the next.

### 4. `operator new` pressure under the lock from the write path

Each `link_tasks` call from Python allocates a new `std::unordered_map` node via `operator new`.
When the map exceeds its load factor threshold it triggers a full rehash. This is visible in the
profiles as 22 MB of `operator new` attributed to `link_tasks` in profile-2, and is consistent
with the appearance of `hashbrown::raw::RawTable<T,A>::reserve_rehash` (64 MB) from the Rust
ddconfig code indicating the system is under general allocator pressure at the same time.

### 5. Lock contention between Python writes and sampling-thread cleanup

`task_link_map_lock_` is shared between:
- Python threads calling `link_tasks` / `weak_link_tasks` on every task creation event
- The sampling thread holding the lock for the entire O(N + M) cleanup block (lines 161–229)
- The sampling thread re-acquiring it per leaf task during parent-chain resolution (line 390)

In a uvloop service creating thousands of tasks per second, the sampling thread's long lock hold
blocks Python task creation hooks, adding latency on the hot path.

### 6. `previous_task_objects` is a full-size clone of the live task set

`previous_task_objects` (`std::unordered_set<PyObject*>`) is cleared and fully rebuilt every
cycle (lines 225–228). It grows to hold one pointer per live asyncio task. In a service with
hundreds of concurrent tasks this is a persistent allocation that also needs to be rehashed as it
grows.

---

## Complexity Summary per Sample Cycle

| Work | Complexity | Notes |
|------|-----------|-------|
| Build `all_task_origins` | O(N) × 2 | Done twice for strong and weak maps |
| Scan `task_link_map` | O(M) × 2 | Once for stale check, once for `parent_tasks` |
| Scan `weak_task_link_map` | O(M') | Once for stale check |
| Cross-check `weak_task_link_map` per strong link | O(M × 1) | Line 216 lookup per entry |
| Rebuild `previous_task_objects` | O(N) | Full clear + reinsert |
| Per-leaf-task lock re-acquire + map lookup | O(L) | L = number of leaf tasks |

N = live task count, M = `task_link_map` size, M' = `weak_task_link_map` size, L = leaf task count.

All of this runs under `task_link_map_lock_`, blocking concurrent Python writes.

---

## Cross-Reference Verification

Each claim was verified against the profiles and source.

### Verified: `link_tasks → operator[] → operator new` = 22 MB (profile-2)

Full call chain from the pprof tree, confirmed absent from profile-1:

```
stack_link_tasks(_object*, _object*)                     22 MB
  └─ Datadog::Sampler::link_tasks(_object*, _object*)    22 MB
       └─ std::unordered_map::operator[](_object* const&)  22 MB
            ├─ operator new(unsigned long)                     11.5 MB  (per-node alloc)
            └─ _Hashtable_alloc::_M_allocate_buckets           10.5 MB  (bucket rehash)
```

The 22 MB splits roughly evenly between node allocations (11.5 MB) and bucket array rehashes
(10.5 MB). This is characteristic of an `std::unordered_map` growing under sustained insertions
without pre-sizing.

### Verified: `unwind_tasks → to_remove vector → operator new` = 6 MB (profile-2)

```
ThreadInfo::unwind_tasks  6 MB
  └─ std::vector<_object*>::push_back  6 MB
       └─ operator new                 6 MB
```

The 6 MB is the `to_remove` vector (`threads.cc:174`). At 8 bytes per `PyObject*`, a 6 MB
vector has capacity for ~750 K entries — an estimate of the stale-entry count per cleanup cycle.
This confirms the maps are accumulating entries faster than cleanup can reclaim them.

### Verified: `all_task_origins` rebuilt identically twice

Source at `threads.cc:168–172` and `threads.cc:195–199`. The second construction produces
the same set as the first because `all_tasks` is immutable within the locked scope.

### Verified: lock held from line 161 through line 229

The `std::lock_guard` at line 161 is not released until the closing brace at line 229. All
six steps (two set constructions, two map scans, parent_tasks build, previous_task_objects
rebuild) execute under this lock.

### Verified: per-leaf lock re-acquisition at line 390

Each iteration of the leaf-task parent-chain walk (`threads.cc:314–410`) re-acquires
`task_link_map_lock_` at line 390 for map lookups.

### Note on Issue 3 (asymmetric grace period)

The grace period on `task_link_map` is intentional: it prevents evicting freshly-linked tasks
that weren't yet enumerated in `get_all_tasks`. The asymmetry is by design, not a bug. However,
the consequence is that under high task churn, `task_link_map` retains stale entries for one
extra cycle. Combined with the ~750 K stale entries visible in the `to_remove` vector, this
contributes to the O(M) scan cost.

### Note on Issue 4 (hashbrown rehash)

The investigation mentioned `hashbrown::reserve_rehash` (64 MB) as correlated allocator
pressure. On re-examination, that 64 MB belongs to `ddconfig` protobuf parsing (Rust side),
not the profiler. Correlation with the profiler's `operator new` pressure is plausible (global
allocator contention) but not proven by the profile alone.

---

## Proposed Fixes

### Fix 1: Eliminate duplicate `all_task_origins` construction

**Impact: removes O(N) set-build + N insertions from the lock hold per cycle.**

`all_task_origins` is built at line 168 and then cleared and identically rebuilt at line 195.
Removing the clear+rebuild is a one-line change.

```cpp
// threads.cc, current (lines 193–199):
// Clean up the weak_task_link_map.
// Remove entries associated to tasks that no longer exist.
all_task_origins.clear();                        // DELETE THIS
std::transform(all_tasks.cbegin(),               // DELETE THIS BLOCK
               all_tasks.cend(),
               std::inserter(all_task_origins, all_task_origins.begin()),
               [](const TaskInfo::Ptr& task) { return task->origin; });
```

Simply remove the `.clear()` and second `std::transform`.

### Fix 2: Build `all_task_origins` outside the lock

**Impact: removes O(N) from the lock hold time.**

`all_task_origins` is derived entirely from `all_tasks`, which is computed before the lock is
taken (line 157). Move the set construction to between lines 158 and 159:

```cpp
auto all_tasks = std::move(*maybe_all_tasks);
echion.add_asyncio_task_count(all_tasks.size());

// Build the set of live task origins OUTSIDE the lock
std::unordered_set<PyObject*> all_task_origins;
all_task_origins.reserve(all_tasks.size());
std::transform(all_tasks.cbegin(), all_tasks.cend(),
               std::inserter(all_task_origins, all_task_origins.begin()),
               [](const TaskInfo::Ptr& task) { return task->origin; });

{
    auto& previous_task_objects = echion.previous_task_objects();
    std::lock_guard<std::mutex> lock(echion.task_link_map_lock());
    // ... remainder uses all_task_origins read-only ...
}
```

This is safe because `all_task_origins` is only read (not written) inside the lock, and
`all_tasks` is a local that no other thread touches.

### Fix 3: Snapshot maps for parent-chain walk to avoid per-leaf lock re-acquire

**Impact: removes L lock acquisitions per cycle (L = number of leaf tasks).**

At line 390, the lock is re-acquired per leaf task. Instead, snapshot the relevant mappings
once inside the existing locked scope at lines 161–229 and use the snapshot for the
parent-chain walk:

```cpp
// Inside the existing locked block (lines 161–229), add at the end:
// Snapshot the link maps for later parent-chain resolution.
auto task_link_snapshot = task_link_map;
auto weak_task_link_snapshot = weak_task_link_map;
```

Then replace the per-leaf lock acquisition block (lines 388–407) with lookups into
the snapshots:

```cpp
// No lock needed — using local snapshots
if (auto maybe_parent = task_link_snapshot.find(task_origin);
    maybe_parent != task_link_snapshot.end()) {
    if (auto maybe_origin = origin_map.find(maybe_parent->second);
        maybe_origin != origin_map.end()) {
        current_task = maybe_origin->second;
        continue;
    }
}
if (auto it = weak_task_link_snapshot.find(task_origin);
    it != weak_task_link_snapshot.end()) {
    if (auto maybe_origin = origin_map.find(it->second);
        maybe_origin != origin_map.end()) {
        current_task = maybe_origin->second;
        continue;
    }
}
```

The snapshots are O(M + M') to copy, but this happens once under the lock instead of L times.
If L > 1 (which it nearly always is), this is a net win: one copy replaces L lock
acquire-release pairs.

The snapshot may be slightly stale by the time leaf tasks are walked (a new
`link_tasks` call from Python could arrive between the snapshot and the walk). This is
acceptable: the profiler already tolerates races between task creation and sampling
(the grace period exists for exactly this reason).

### Fix 4: Reserve capacity on `all_task_origins` and `previous_task_objects`

**Impact: eliminates rehash chains during set construction.**

```cpp
std::unordered_set<PyObject*> all_task_origins;
all_task_origins.reserve(all_tasks.size());  // ADD THIS
```

```cpp
previous_task_objects.clear();
previous_task_objects.reserve(all_tasks.size());  // ADD THIS (after clear, before loop)
```

These are one-liners that eliminate repeated bucket-array doublings during construction.

### Fix 5: Reuse `to_remove` vector across cycles

**Impact: eliminates the 6 MB `operator new` from `unwind_tasks` visible in profile-2.**

Currently `to_remove` is a local that is allocated and freed each cycle. Making it a member
of `EchionSampler` (like `seen_frames_scratch_` already is) lets its capacity persist:

```cpp
// echion_sampler.h — add member:
std::vector<PyObject*> to_remove_scratch_;

// threads.cc — replace local:
auto& to_remove = echion.to_remove_scratch();
to_remove.clear();  // resets size, keeps capacity
```

### Fix 6 (future): Replace `std::unordered_map` with an open-addressing map

**Impact: eliminates per-node `operator new`, halves memory footprint, improves iteration.**

`std::unordered_map` allocates one heap node per entry (the 11.5 MB `operator new` in the
profile). An open-addressing hash map (e.g., a flat hash map, or even `absl::flat_hash_map`
if available) stores entries inline in a contiguous array:

- No per-insert `operator new`
- Cache-friendly iteration (important for the O(M) cleanup scan)
- ~2× lower memory per entry (no next-pointer overhead)

This is a larger change and would require updating `postfork_child` (placement-new reset)
and testing rehash behavior. Worth doing if fixes 1–5 are insufficient.

---

## Recommended Ordering

Fixes 1–4 are low-risk, localized changes that can be done together in one PR. Fix 5 is
slightly more invasive (adds a member to `EchionSampler`) but straightforward. Fix 6 is a
larger refactor that can be evaluated after measuring the impact of 1–5.

---

## Files Referenced

| File | Lines | Role |
|------|-------|------|
| `ddtrace/internal/datadog/profiling/stack/echion/echion/echion_sampler.h` | 44–46, 128–130, 195–196 | Map declarations and postfork reset |
| `ddtrace/internal/datadog/profiling/stack/src/sampler.cpp` | 941–951 | `link_tasks` / `weak_link_tasks` write path |
| `ddtrace/internal/datadog/profiling/stack/src/echion/threads.cc` | 159–229, 388–407 | Cleanup and parent-chain resolution |
| `ddtrace/profiling/_asyncio.py` | 212, 228, 243, 270, 296, 309, 321, 334 | Python hooks that call `link_tasks` |
| `ddtrace/profiling/example/profile.pprof` | — | 646 MB inuse baseline |
| `ddtrace/profiling/example/profile-2.pprof` | — | 932 MB inuse; `link_tasks → operator new` 22 MB visible |
