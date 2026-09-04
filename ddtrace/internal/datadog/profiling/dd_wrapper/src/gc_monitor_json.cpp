#include "gc_monitor_json.hpp"

#include <chrono>
#include <cstdio>
#include <sstream>
#include <string>

namespace Datadog {

namespace {

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

} // anonymous namespace

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
                        const std::vector<uint32_t>& roots)
{
    auto now = std::chrono::system_clock::now();
    auto ts_ns =
      static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count());

    std::ostringstream out;
    out << "{\"v\":2"
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

    // First-order type reference graph. Only present when referrers are enabled
    // (i.e. roots is non-empty). Consumers reconstruct any tree they want from
    // it; nothing is unrolled natively.
    if (!roots.empty()) {
        // per-type total shallow bytes (parallel array to "tt")
        out << ",\"tsz\":[";
        for (size_t i = 0; i < type_sizes.size(); ++i) {
            if (i > 0) {
                out << ",";
            }
            out << type_sizes[i];
        }
        out << "]";

        // adjacency: holder type index -> list of [held_idx, ic, ts] edges.
        // A holder's ic/ts as a *root* come from tc/tsz; the ic/ts on each edge
        // are the held instance count reached from the holder type and the
        // shallow bytes they retain (each held object's own shallow size plus
        // the generic-container scaffold it owns). Holders with no edges are
        // omitted.
        out << ",\"g\":{";
        bool first_holder = true;
        for (size_t h = 0; h < adjacency.size(); ++h) {
            const auto& edges = adjacency[h];
            if (edges.empty()) {
                continue;
            }
            if (!first_holder) {
                out << ",";
            }
            first_holder = false;
            out << "\"" << h << "\":[";
            for (size_t e = 0; e < edges.size(); ++e) {
                if (e > 0) {
                    out << ",";
                }
                out << "[" << edges[e].held_idx << "," << edges[e].ic << "," << edges[e].ts << "]";
            }
            out << "]";
        }
        out << "}";

        // ordered reconstruction roots (type indices, heaviest retained first)
        out << ",\"roots\":[";
        for (size_t i = 0; i < roots.size(); ++i) {
            if (i > 0) {
                out << ",";
            }
            out << roots[i];
        }
        out << "]";
    }

    out << "}";

    return out.str();
}

} // namespace Datadog
