#pragma once

#include "gc_monitor.hpp"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace Datadog {

// Serialize a GC snapshot into the wire JSON string used by GCMonitor.
// Pure C++: no Python API, no locking. Safe to call with the GIL released.
std::string
serialize_snapshot_json(const std::array<GCGenStats, 3>& gen_stats,
                        const std::array<GCGenStats, 3>& delta_stats,
                        bool gc_enabled,
                        const std::array<int, 3>& thresholds,
                        int garbage_count,
                        const std::vector<std::string>& type_table,
                        const std::vector<uint32_t>& type_counts,
                        const std::vector<RootNode>& roots,
                        const std::vector<TreeNode>& ref_tree);

} // namespace Datadog
