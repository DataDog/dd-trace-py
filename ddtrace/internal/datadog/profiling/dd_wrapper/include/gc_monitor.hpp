#pragma once

#include <array>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace Datadog {

struct GCGenStats
{
    uint64_t n{ 0 };     // collection count
    uint64_t col{ 0 };   // objects collected
    uint64_t uncol{ 0 }; // uncollectable objects
};

// One first-order edge in the type reference graph: "this holder type retains
// `ic` instances of `held_idx`, together retaining `ts` shallow bytes". The
// producer emits the flat graph (see GCMonitor::serialize) and the consumer
// reconstructs any tree it wants from it -- no tree is materialized natively.
struct GCRefEdge
{
    uint32_t held_idx{ 0 }; // index into the type table of the held type
    uint64_t ic{ 0 };       // number of held instances reached from the holder type
    uint64_t ts{ 0 };       // shallow bytes those held instances retain
};

class GCMonitor
{
  public:
    static GCMonitor& get();

    // Start the background thread with the given configuration.
    // Calling start() when already running is a no-op.
    void start(uint64_t interval_ms, int survivor_threshold, int top_n, bool referrers_enabled, int max_depth);

    // Signal the background thread to stop and return immediately.
    // No final snapshot is taken. Safe to call from any thread.
    void stop();

    // Return a copy of the latest serialized JSON snapshot, or an empty
    // string if no snapshot has been taken yet.
    std::string get_latest_json() const;

    // pthread_atfork handlers. Installed on the first successful start().
    // prefork:         held on the parent so fork() sees quiescent state.
    // postfork_parent: releases what prefork acquired, parent thread lives on.
    // postfork_child:  the parent's std::thread does not exist in the child.
    //                  Reset the sync primitives via placement-new (they may
    //                  be inherited in an undefined state), clear the state,
    //                  and re-arm a fresh sampling thread if we were running.
    void prefork();
    void postfork_parent();
    void postfork_child();

  private:
    GCMonitor() = default;
    GCMonitor(const GCMonitor&) = delete;
    GCMonitor& operator=(const GCMonitor&) = delete;

    void thread_main();
    void take_snapshot();
    void install_atfork_once();

    // Serialize the reference graph + gc stats to the output JSON string.
    // Called with GIL already released. `adjacency` is parallel to `type_table`
    // (adjacency[holder] = its first-order edges); `type_sizes` is the per-type
    // total shallow bytes; `roots` is the ordered set of types to expose as
    // reconstruction roots. The last three are empty when referrers are off.
    void serialize(const std::array<GCGenStats, 3>& gen_stats,
                   const std::array<GCGenStats, 3>& delta_stats,
                   bool gc_enabled,
                   const std::array<int, 3>& thresholds,
                   int garbage_count,
                   const std::vector<std::string>& type_table,
                   const std::vector<uint32_t>& type_counts,
                   const std::vector<uint64_t>& type_sizes,
                   const std::vector<std::vector<GCRefEdge>>& adjacency,
                   const std::vector<uint32_t>& roots);

    std::thread _thread;
    mutable std::mutex _mutex;
    std::condition_variable _cv;
    bool _stop_flag{ false };
    bool _started{ false };

    uint64_t _interval_ms{ 60000 };
    int _survivor_threshold{ 3 };
    int _top_n{ 20 };
    bool _referrers_enabled{ false };
    // Retained for config/ABI compatibility. The producer no longer materializes
    // a tree (it emits the first-order graph); depth is now a consumer concern.
    int _max_depth{ 10 };

    // Cross-snapshot state (only accessed from the background thread)
    std::array<GCGenStats, 3> _prev_gen_stats{};

    std::string _latest_json; // protected by _mutex

    // Snapshot of _started captured in prefork() so postfork_child() can
    // decide whether to re-arm the sampling thread; postfork_child() clears
    // _started itself as part of resetting per-process state.
    bool _was_running_at_fork{ false };
};

} // namespace Datadog
