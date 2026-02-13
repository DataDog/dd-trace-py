#include "libdatadog_helpers.hpp"

#include <atomic>

namespace {

// These caches store interned string IDs for tag/label keys.
// Using std::atomic with relaxed memory order for correctness under concurrent access.
// This is a benign race: the worst case is interning a string twice, which is harmless
// since the Profiles Dictionary owns all interned strings until it's released.
std::array<std::atomic<ddog_prof_StringId2>, static_cast<size_t>(Datadog::ExportTagKey::Length_)> tag_cache{};
std::array<std::atomic<ddog_prof_StringId2>, static_cast<size_t>(Datadog::ExportLabelKey::Length_)> label_cache{};

} // namespace

namespace Datadog::internal {

void
reset_key_caches()
{
    for (auto& entry : tag_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
    for (auto& entry : label_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
}

std::optional<ddog_prof_StringId2>
to_interned_string(ExportTagKey key)
{
    const auto idx = static_cast<size_t>(key);

    if (idx >= tag_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - see comment above cache declaration)
    auto string_id = tag_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        tag_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

std::optional<ddog_prof_StringId2>
to_interned_string(ExportLabelKey key)
{
    const auto idx = static_cast<size_t>(key);

    if (idx >= label_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - see comment above cache declaration)
    auto string_id = label_cache[idx].load(std::memory_order_relaxed);
    if (string_id == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        label_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}
} // namespace Datadog::internal
