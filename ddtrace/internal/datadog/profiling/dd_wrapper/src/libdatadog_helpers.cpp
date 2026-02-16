#include "libdatadog_helpers.hpp"

#include "ddup.hpp"
#include "sample.hpp"

namespace Datadog::internal {

std::optional<ddog_prof_StringId2>
to_interned_string(ExportTagKey key)
{
    auto& ddup = Ddup::get();
    const auto idx = static_cast<size_t>(key);

    if (idx >= ddup.tag_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - benign race, worst case is interning twice)
    auto string_id = ddup.tag_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        ddup.tag_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

std::optional<ddog_prof_StringId2>
to_interned_string(ExportLabelKey key)
{
    auto& ddup = Ddup::get();
    const auto idx = static_cast<size_t>(key);

    if (idx >= ddup.label_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - benign race, worst case is interning twice)
    auto string_id = ddup.label_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        ddup.label_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

} // namespace Datadog::internal
