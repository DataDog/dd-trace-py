#pragma once

#include "gc_monitor.hpp"

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace Datadog {

// Serialize a GC snapshot into the wire JSON string used by GCMonitor.
// Pure C++: no Python API, no locking. Safe to call with the GIL released.
//
// Emits the first-order type reference graph (schema v2): the type table `tt`,
// the per-type instance counts `tc` and shallow sizes `tsz` (parallel to `tt`),
// the adjacency `g` (holder type index -> its `[held, ic, ts]` edges) and the
// ordered reconstruction roots `roots`. Consumers rebuild any tree they want
// from this graph. `type_sizes`, `adjacency` and `roots` are empty (and their
// keys omitted) when referrers are disabled.
std::string
serialize_snapshot_json(const std::array<GCGenStats, 3>& gen_stats,
                        const std::array<GCGenStats, 3>& delta_stats,
                        bool gc_enabled,
                        const std::array<int, 3>& thresholds,
                        int garbage_count,
                        const std::vector<std::string>& type_table,
                        const std::vector<uint32_t>& type_counts,
                        const std::vector<uint64_t>& type_sizes,
                        const std::vector<std::vector<GCRefEdge>>& adjacency,
                        const std::vector<uint32_t>& roots);

} // namespace Datadog
