#include "libdatadog_helpers.hpp"

#include "profiler_state.hpp"
#include "sample.hpp"

namespace Datadog::internal {

std::optional<ddog_prof_StringId2>
to_interned_string(ExportTagKey key)
{
    auto& state = ProfilerState::get();
    const auto idx = static_cast<size_t>(key);

    if (idx >= state.tag_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - benign race, worst case is interning twice)
    auto string_id = state.tag_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        state.tag_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

std::optional<ddog_prof_StringId2>
to_interned_string(ExportLabelKey key)
{
    auto& state = ProfilerState::get();
    const auto idx = static_cast<size_t>(key);

    if (idx >= state.label_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - benign race, worst case is interning twice)
    auto string_id = state.label_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        state.label_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

} // namespace Datadog::internal
