#pragma once

#include <array>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace Datadog {

struct GCGenStats
{
    uint64_t n{ 0 };     // collection count
    uint64_t col{ 0 };   // objects collected
    uint64_t uncol{ 0 }; // uncollectable objects
};

struct TreeNode
{
    uint32_t type_idx{ 0 };
    uint64_t ic{ 0 }; // instance count
    uint64_t ts{ 0 }; // total shallow size (bytes)
    std::vector<TreeNode> children;

    // Merge another node's stats into this one; children are NOT merged here.
    void add(uint64_t instances, uint64_t size)
    {
        ic += instances;
        ts += size;
    }
};

struct RootNode : TreeNode
{
    char category{ '?' }; // K S F O ?
    std::string fn;       // module.var or funcname:var (file:line); may be empty
};

class GCMonitor
{
  public:
    static GCMonitor& get();

    // Start the background thread with the given configuration.
    // Calling start() when already running is a no-op.
    void start(uint64_t interval_ms, int survivor_threshold, int top_n, bool referrers_enabled);

    // Signal the background thread to stop and return immediately.
    // No final snapshot is taken. Safe to call from any thread.
    void stop();

    // Return a copy of the latest serialized JSON snapshot, or an empty
    // string if no snapshot has been taken yet.
    std::string get_latest_json() const;

  private:
    GCMonitor() = default;
    GCMonitor(const GCMonitor&) = delete;
    GCMonitor& operator=(const GCMonitor&) = delete;

    void thread_main();
    void take_snapshot();

    // Serialize the tree + gc stats to the output JSON string.
    // Called with GIL already released.
    void serialize(const std::array<GCGenStats, 3>& gen_stats,
                   const std::array<GCGenStats, 3>& delta_stats,
                   bool gc_enabled,
                   const std::array<int, 3>& thresholds,
                   int garbage_count,
                   const std::vector<std::string>& type_table,
                   const std::vector<uint32_t>& type_counts,
                   const std::vector<RootNode>& roots);

    std::thread _thread;
    mutable std::mutex _mutex;
    std::condition_variable _cv;
    bool _stop_flag{ false };
    bool _started{ false };

    uint64_t _interval_ms{ 60000 };
    int _survivor_threshold{ 3 };
    int _top_n{ 20 };
    bool _referrers_enabled{ false };

    // Cross-snapshot state (only accessed from the background thread)
    std::array<GCGenStats, 3> _prev_gen_stats{};
    // object id -> consecutive snapshots in which the object appeared
    std::unordered_map<uintptr_t, int> _survivor_counts;
    // object id -> (type_idx, shallow_size) from the previous snapshot
    std::unordered_map<uintptr_t, std::pair<uint32_t, uint64_t>> _prev_objs;

    std::string _latest_json; // protected by _mutex
};

} // namespace Datadog
