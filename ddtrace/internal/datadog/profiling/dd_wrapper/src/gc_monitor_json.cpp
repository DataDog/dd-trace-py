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

} // anonymous namespace

std::string
serialize_snapshot_json(const std::array<GCGenStats, 3>& gen_stats,
                        const std::array<GCGenStats, 3>& delta_stats,
                        bool gc_enabled,
                        const std::array<int, 3>& thresholds,
                        int garbage_count,
                        const std::vector<std::string>& type_table,
                        const std::vector<uint32_t>& type_counts,
                        const std::vector<TreeNode>& ref_tree)
{
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

    // reference tree (type -> type "holds a reference to" graph). Each node is
    // {"t":type_idx,"ic":refs,"ts":bytes,"ch":[...]} -- see serialize_node. A
    // root node's ic/ts are the type's live instance count and total shallow
    // size; a child node's ic/ts are the number of references from the parent
    // type to the child type and the bytes they retain (each held object's own
    // shallow size plus the generic-container scaffold it owns).
    out << ",\"rt\":[";
    for (size_t i = 0; i < ref_tree.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        serialize_node(out, ref_tree[i], 0);
    }
    out << "]}";

    return out.str();
}

} // namespace Datadog
