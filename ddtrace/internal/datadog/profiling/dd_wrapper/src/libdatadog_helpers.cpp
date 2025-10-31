#include "libdatadog_helpers.hpp"

namespace {

std::array<ddog_prof_StringId2, static_cast<size_t>(Datadog::ExportTagKey::Length_)> tag_cache{};
std::array<ddog_prof_StringId2, static_cast<size_t>(Datadog::ExportLabelKey::Length_)> label_cache{};

} // namespace

namespace Datadog::internal {

void
reset_key_caches()
{
    tag_cache.fill(nullptr);
    label_cache.fill(nullptr);
}

ddog_prof_StringId2
to_interned_string(ExportTagKey key)
{
    const auto idx = static_cast<size_t>(key);

    if (idx >= tag_cache.size()) {
        return nullptr;
    }

    // Check cache first
    auto string_id = tag_cache[idx];
    if (string_id == nullptr) {
        string_id = intern_string(to_string(key));
        tag_cache[idx] = string_id;
        return string_id;
    }

    return string_id;
}

ddog_prof_StringId2
to_interned_string(ExportLabelKey key)
{
    const auto idx = static_cast<size_t>(key);

    if (idx >= label_cache.size()) {
        return nullptr;
    }

    // Check cache first
    auto string_id = label_cache[idx];
    if (string_id == nullptr) {
        string_id = intern_string(to_string(key));
        label_cache[idx] = string_id;
        return string_id;
    }

    return string_id;
}
} // namespace Datadog::internal
